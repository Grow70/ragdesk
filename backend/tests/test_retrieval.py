"""Exact cosine ranking and authorization against disposable PostgreSQL."""

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic import command
from app.llm.contracts import EmbeddingResult, ModelError, ModelUsage
from app.llm.fake import FakeEmbeddingClient
from app.llm.openai import OpenAIEmbeddingClient
from app.main import create_app
from app.models import Chunk, Document, DocumentBuild, KBMember, KnowledgeBase, User
from app.services.auth import create_access_token, hash_password
from app.services.ingest import EmbeddingProfile

SECRET = "retrieval-test-jwt-secret-at-least-32-bytes"
PROFILE = EmbeddingProfile.fake()


class QueryFake:
    provider = "fake"
    model = "sha256-onehot-v1"
    dimensions = 1536
    call_count = 0

    def embed_query(self, text):
        self.call_count += 1
        return EmbeddingResult([[1.0] + [0.0] * 1535], ModelUsage(), 0.0, 1)


@pytest.fixture
def retrieval_db(monkeypatch):
    admin_url = os.getenv("TEST_POSTGRES_ADMIN_URL")
    if not admin_url:
        pytest.skip("Set TEST_POSTGRES_ADMIN_URL for disposable PostgreSQL test")
    name = f"ragdesk_retrieval_{uuid4().hex}"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    engine = create_engine(url)
    try:
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("JWT_SECRET", SECRET)
        command.upgrade(
            Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
        )
        ids = {name: uuid4() for name in ("alice", "bob", "a", "b", "empty")}
        with Session(engine) as session:
            session.add_all(
                User(
                    id=ids[name],
                    display_name=name,
                    login_name=name,
                    password_hash=hash_password("test-password"),
                )
                for name in ("alice", "bob")
            )
            session.add_all(
                KnowledgeBase(id=ids[name], name=name) for name in ("a", "b", "empty")
            )
            session.flush()
            session.add_all(
                [
                    KBMember(user_id=ids["alice"], kb_id=ids["a"], role="member"),
                    KBMember(user_id=ids["alice"], kb_id=ids["empty"], role="member"),
                    KBMember(user_id=ids["bob"], kb_id=ids["b"], role="admin"),
                ]
            )
            session.commit()
        yield engine, ids
    finally:
        engine.dispose()
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin_engine.dispose()


def _seed(
    engine,
    kb_id,
    name,
    vector,
    *,
    status="ready",
    active=True,
    deleted=False,
    config_id=None,
    page=1,
):
    doc_id, build_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(
            Document(
                id=doc_id,
                kb_id=kb_id,
                file_name=f"{name}.md",
                file_sha256=hashlib.sha256(name.encode()).hexdigest(),
                storage_key=f"objects/{uuid4().hex}",
            )
        )
        session.flush()
        session.add(
            DocumentBuild(
                id=build_id,
                document_id=doc_id,
                status=status,
                parser_config={},
                chunking_config={},
                model_config_id=config_id or PROFILE.config_id,
                error_code="TEST_FAILURE" if status == "failed" else None,
            )
        )
        session.flush()
        chunk = Chunk(
            build_id=build_id,
            ordinal=0,
            body=name,
            content_sha256=hashlib.sha256(name.encode()).hexdigest(),
            page_number=page,
            heading_path=["演示标题"],
            embedding=vector,
        )
        session.add(chunk)
        doc = session.get(Document, doc_id)
        if active:
            doc.active_build_id = build_id
        if deleted:
            from datetime import datetime, timezone

            doc.deleted_at = datetime.now(timezone.utc)
        session.commit()
        return chunk.id


def _headers(user_id):
    return {"Authorization": f"Bearer {create_access_token(user_id, SECRET, 30)}"}


def _app(fake):
    app = create_app()
    app.state.retrieval_profile = PROFILE
    app.state.retrieval_client_factory = lambda: fake
    return app


def test_cosine_order_scope_and_limits(retrieval_db):
    engine, ids = retrieval_db
    near = _seed(engine, ids["a"], "near", [1.0] + [0.0] * 1535)
    mid = _seed(engine, ids["a"], "mid", [0.8, 0.6] + [0.0] * 1534, page=2)
    far = _seed(engine, ids["a"], "far", [0.0, 1.0] + [0.0] * 1534)
    _seed(engine, ids["b"], "private", [1.0] + [0.0] * 1535)
    _seed(engine, ids["a"], "deleted", [1.0] + [0.0] * 1535, deleted=True)
    _seed(engine, ids["a"], "inactive", [1.0] + [0.0] * 1535, active=False)
    _seed(engine, ids["a"], "failed", [1.0] + [0.0] * 1535, status="failed")
    _seed(
        engine,
        ids["a"],
        "other-model",
        [1.0] + [0.0] * 1535,
        config_id="fake:other:1536:raw-v1",
    )
    fake = QueryFake()
    with TestClient(_app(fake)) as client:
        path = f"/knowledge-bases/{ids['a']}/search"
        result = client.post(
            path, json={"query": "测试"}, headers=_headers(ids["alice"])
        )
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["distance_metric"] == "cosine_distance"
        assert body["request_id"]
        assert [row["chunk_id"] for row in body["items"]] == [
            str(near),
            str(mid),
            str(far),
        ]
        assert [row["rank"] for row in body["items"]] == [1, 2, 3]
        assert [row["distance"] for row in body["items"]] == pytest.approx([0, 0.2, 1])
        assert body["items"][1]["page_number"] == 2
        assert body["items"][1]["heading_path"] == ["演示标题"]
        assert body["items"][1]["document_name"] == "mid.md"
        assert body["items"][1]["text"] == "mid"
        limited = client.post(
            path, json={"query": "测试", "top_k": 1}, headers=_headers(ids["alice"])
        )
        assert len(limited.json()["items"]) == 1
        assert (
            client.post(
                path,
                json={"query": "测试", "top_k": 20},
                headers=_headers(ids["alice"]),
            ).status_code
            == 200
        )
        for top_k in (0, 21, True):
            assert (
                client.post(
                    path,
                    json={"query": "测试", "top_k": top_k},
                    headers=_headers(ids["alice"]),
                ).status_code
                == 422
            )
        empty = client.post(
            f"/knowledge-bases/{ids['empty']}/search",
            json={"query": "测试"},
            headers=_headers(ids["alice"]),
        )
        assert empty.status_code == 200 and empty.json()["items"] == []


def test_auth_precedes_embedding_and_removed_member_loses_access(retrieval_db):
    engine, ids = retrieval_db
    fake = QueryFake()
    alice_headers = _headers(ids["alice"])
    bob_headers = _headers(ids["bob"])
    with TestClient(_app(fake)) as client:
        path = f"/knowledge-bases/{ids['a']}/search"
        assert client.post(path, json={"query": "测试"}).status_code == 401
        forbidden = client.post(path, json={"query": "测试"}, headers=bob_headers)
        absent = client.post(
            f"/knowledge-bases/{uuid4()}/search",
            json={"query": "测试"},
            headers=bob_headers,
        )
        assert forbidden.status_code == absent.status_code == 404
        assert forbidden.json()["error"] == absent.json()["error"]
        assert fake.call_count == 0
        with Session(engine) as session:
            membership = session.scalar(
                select(KBMember).where(
                    KBMember.user_id == ids["alice"], KBMember.kb_id == ids["a"]
                )
            )
            session.delete(membership)
            session.commit()
        assert (
            client.post(path, json={"query": "测试"}, headers=alice_headers).status_code
            == 404
        )
        assert fake.call_count == 0


def test_model_failure_is_error_not_empty_result(retrieval_db):
    engine, ids = retrieval_db
    _seed(engine, ids["a"], "indexed", [1.0] + [0.0] * 1535)

    class FailingFake(QueryFake):
        def embed_query(self, text):
            raise ModelError("MODEL_TIMEOUT", attempts=3)

    with TestClient(_app(FailingFake())) as client:
        response = client.post(
            f"/knowledge-bases/{ids['a']}/search",
            json={"query": "测试"},
            headers=_headers(ids["alice"]),
        )
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "MODEL_TIMEOUT"

    class InvalidFake(QueryFake):
        def embed_query(self, text):
            return EmbeddingResult([[0.0] * 1536], ModelUsage(), 0.0, 1)

    with TestClient(_app(InvalidFake())) as client:
        response = client.post(
            f"/knowledge-bases/{ids['a']}/search",
            json={"query": "测试"},
            headers=_headers(ids["alice"]),
        )
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "MODEL_INVALID_VECTOR"


def test_explicit_fake_runtime_and_missing_real_key(retrieval_db, monkeypatch):
    engine, ids = retrieval_db
    vector = FakeEmbeddingClient().embed_query("精确查询").vectors[0]
    _seed(engine, ids["a"], "fake-index", vector)
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "fake")
    with TestClient(create_app()) as client:
        response = client.post(
            f"/knowledge-bases/{ids['a']}/search",
            json={"query": "精确查询"},
            headers=_headers(ids["alice"]),
        )
    assert response.status_code == 200
    assert response.json()["items"][0]["distance"] == pytest.approx(0)

    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _seed(
        engine,
        ids["a"],
        "real-config",
        vector,
        config_id="openai:text-embedding-3-small:1536:raw-v1",
    )
    with TestClient(create_app()) as client:
        response = client.post(
            f"/knowledge-bases/{ids['a']}/search",
            json={"query": "精确查询"},
            headers=_headers(ids["alice"]),
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"


def test_real_semantic_retrieval_opt_in(retrieval_db):
    """Two real embedding calls; report ordering separately from fake checks."""
    if os.getenv("RUN_REAL_RETRIEVAL") != "1":
        pytest.skip("Set RUN_REAL_RETRIEVAL=1 to allow real paid model calls")
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        pytest.skip("OPENAI_API_KEY is absent; real semantic retrieval unverified")
    engine, ids = retrieval_db
    profile = EmbeddingProfile(provider="openai", model="text-embedding-3-small")
    texts = [
        "演示数据：差旅报销须在返程后 10 个自然日内提交票据。",
        "演示数据：运维值班发生服务故障时，先查看监控告警并记录事件。",
    ]
    embedding = OpenAIEmbeddingClient(api_key=key, max_attempts=1)
    try:
        vectors = embedding.embed_documents(texts).vectors
    finally:
        embedding.close()
    for index, vector in enumerate(vectors):
        _seed(engine, ids["a"], texts[index], vector, config_id=profile.config_id)
    app = create_app()
    app.state.retrieval_profile = profile
    app.state.retrieval_client_factory = lambda: OpenAIEmbeddingClient(
        api_key=key, max_attempts=1
    )
    with TestClient(app) as client:
        response = client.post(
            f"/knowledge-bases/{ids['a']}/search",
            json={"query": "出差回来后，票据要在几天内报销？", "top_k": 2},
            headers=_headers(ids["alice"]),
        )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    print(
        "real_semantic_distances", [(item["rank"], item["distance"]) for item in items]
    )
    assert len(items) == 2
    assert items[0]["text"] == texts[0]
    assert items[0]["distance"] <= items[1]["distance"]
