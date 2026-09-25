"""Disposable PostgreSQL checks for one-document indexing and publication."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.ingest_document import main as ingest_main
from app.llm.contracts import ModelError
from app.llm.fake import FakeEmbeddingClient
from app.models import Chunk, Document, DocumentBuild, KnowledgeBase
from app.repositories.chunks import searchable_chunks_stmt
from app.services.ingest import EmbeddingProfile, ingest_document


@pytest.fixture
def database(monkeypatch, tmp_path):
    admin_url = os.getenv("TEST_POSTGRES_ADMIN_URL")
    if not admin_url:
        pytest.skip("Set TEST_POSTGRES_ADMIN_URL for disposable PostgreSQL test")
    name = f"ragdesk_ingest_{uuid4().hex}"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    db_url = (
        make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    )
    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    engine = create_engine(db_url)
    try:
        monkeypatch.setenv("DATABASE_URL", db_url)
        monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-of-at-least-32-bytes-long")
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(config, "head")
        factory = sessionmaker(engine)
        kb_id = uuid4()
        storage = tmp_path / "uploads"
        (storage / "objects").mkdir(parents=True)
        with factory.begin() as session:
            session.add(KnowledgeBase(id=kb_id, name="A"))
        yield engine, factory, kb_id, storage, config
    finally:
        engine.dispose()
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin_engine.dispose()


def _document(factory, kb_id, storage, text):
    doc_id = uuid4()
    key = f"objects/{uuid4().hex}"
    data = text.encode("utf-8")
    (storage / key).write_bytes(data)
    with factory.begin() as session:
        session.add(
            Document(
                id=doc_id,
                kb_id=kb_id,
                file_name="demo.txt",
                file_sha256=hashlib.sha256(data).hexdigest(),
                storage_key=key,
            )
        )
    return doc_id


class CheckedFake(FakeEmbeddingClient):
    def __init__(self, engine, *, fail_call=None, bad_dimension=False):
        super().__init__(dimensions=1536)
        self.engine = engine
        self.fail_call = fail_call
        self.bad_dimension = bad_dimension

    def embed_documents(self, texts):
        assert self.engine.pool.checkedout() == 0  # No DB session during network work.
        if self.fail_call == self.call_count + 1:
            self.call_count += 1
            raise ModelError("MODEL_TIMEOUT", attempts=1)
        result = super().embed_documents(texts)
        if self.bad_dimension:
            result.vectors[0] = [1.0, 0.0]
        return result


def test_complete_publish_and_same_build_is_idempotent(database):
    engine, factory, kb_id, storage, _config = database
    document_id = _document(factory, kb_id, storage, "甲" * 140)
    client = CheckedFake(engine)
    profile = EmbeddingProfile.fake(chunk_size=40, overlap=8, batch_size=2)
    build_id = uuid4()
    first = ingest_document(factory, document_id, storage, client, profile, build_id)
    assert first.status == "ready", first.error_code
    assert first.chunk_count >= 4
    assert client.call_count >= 2
    with Session(engine) as session:
        build = session.get(DocumentBuild, build_id)
        document = session.get(Document, document_id)
        assert build.embedding_model == client.model
        assert build.embedding_dimensions == 1536
        assert build.config_version == profile.config_version
        assert build.chunking_config["chunk_size"] == 40
        assert document.active_build_id == build_id
        chunks = session.scalars(searchable_chunks_stmt(kb_id, profile.config_id)).all()
        assert len(chunks) == first.chunk_count
        assert all(len(chunk.embedding) == 1536 for chunk in chunks)
        assert all(chunk.source_spans for chunk in chunks)
        assert (
            session.scalars(searchable_chunks_stmt(kb_id, "other-config")).all() == []
        )
    second = ingest_document(factory, document_id, storage, client, profile, build_id)
    assert second.reused and second.status == "ready"
    assert second.chunk_count == first.chunk_count
    with Session(engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(Chunk)) == first.chunk_count
        )


def test_mid_batch_failure_keeps_old_build_and_partial_chunks_invisible(database):
    engine, factory, kb_id, storage, _config = database
    document_id = _document(factory, kb_id, storage, "甲" * 140)
    profile = EmbeddingProfile.fake(chunk_size=40, overlap=8, batch_size=2)
    old_id = uuid4()
    old_result = ingest_document(
        factory, document_id, storage, CheckedFake(engine), profile, old_id
    )
    assert old_result.status == "ready", old_result.error_code
    failing = CheckedFake(engine, fail_call=2)
    failed_id = uuid4()
    result = ingest_document(factory, document_id, storage, failing, profile, failed_id)
    assert result.status == "failed" and result.error_code == "MODEL_TIMEOUT"
    assert result.chunk_count == 2
    with Session(engine) as session:
        assert session.get(Document, document_id).active_build_id == old_id
        assert session.get(DocumentBuild, failed_id).status == "failed"
        assert (
            session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.build_id == failed_id)
            )
            == 2
        )
        assert {
            chunk.build_id
            for chunk in session.scalars(
                searchable_chunks_stmt(kb_id, profile.config_id)
            )
        } == {old_id}
    calls = failing.call_count
    repeated = ingest_document(
        factory, document_id, storage, failing, profile, failed_id
    )
    assert repeated.reused and repeated.chunk_count == 2
    assert failing.call_count == calls


def test_dimension_mismatch_never_publishes(database):
    engine, factory, kb_id, storage, _config = database
    document_id = _document(factory, kb_id, storage, "演示资料。")
    profile = EmbeddingProfile.fake()
    build_id = uuid4()
    result = ingest_document(
        factory,
        document_id,
        storage,
        CheckedFake(engine, bad_dimension=True),
        profile,
        build_id,
    )
    assert result.status == "failed"
    assert result.error_code == "EMBEDDING_DIMENSION_MISMATCH"
    with Session(engine) as session:
        assert session.get(Document, document_id).active_build_id is None
        assert (
            session.scalars(searchable_chunks_stmt(kb_id, profile.config_id)).all()
            == []
        )


def test_incompatible_config_cannot_mix_in_one_live_kb(database):
    engine, factory, kb_id, storage, _config = database
    first_doc = _document(factory, kb_id, storage, "第一份演示资料。")
    second_doc = _document(factory, kb_id, storage, "第二份演示资料。")
    fake_profile = EmbeddingProfile.fake()
    first_result = ingest_document(
        factory, first_doc, storage, CheckedFake(engine), fake_profile, uuid4()
    )
    assert first_result.status == "ready", first_result.error_code
    real_profile = EmbeddingProfile(
        provider="openai", model="text-embedding-3-small", dimensions=1536
    )
    simulated_real = CheckedFake(engine)
    simulated_real.provider = "openai"
    simulated_real.model = "text-embedding-3-small"
    result = ingest_document(
        factory, second_doc, storage, simulated_real, real_profile, uuid4()
    )
    assert result.status == "failed"
    assert result.error_code == "INCOMPATIBLE_KB_INDEX"
    with Session(engine) as session:
        assert session.get(Document, second_doc).active_build_id is None


def test_vector_migration_downgrade_in_disposable_database(database, monkeypatch):
    engine, _factory, _kb_id, _storage, config = database
    from sqlalchemy import inspect

    assert "embedding" in {col["name"] for col in inspect(engine).get_columns("chunks")}
    command.check(config)
    engine.dispose()
    command.downgrade(config, "0002_user_credentials")
    assert "embedding" not in {
        col["name"] for col in inspect(engine).get_columns("chunks")
    }
    command.upgrade(config, "head")


def test_cli_reports_result_and_reuses_explicit_build_id(database, monkeypatch, capsys):
    _engine, factory, _kb_id, storage, _config = database
    kb_id = _kb_id
    document_id = _document(factory, kb_id, storage, "命令行演示资料。")
    build_id = uuid4()
    monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(storage))
    args = [
        "--document-id",
        str(document_id),
        "--build-id",
        str(build_id),
        "--embedding-backend",
        "fake",
    ]
    assert ingest_main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "ready"
    assert first["build_id"] == str(build_id)
    assert first["reused"] is False
    assert ingest_main(args) == 0
    assert json.loads(capsys.readouterr().out)["reused"] is True
    args[1] = str(uuid4())
    assert ingest_main(args) == 2
    assert json.loads(capsys.readouterr().err)["error_code"] == "DOCUMENT_NOT_FOUND"


def test_partial_pdf_is_failed_and_not_published(database):
    engine, factory, kb_id, storage, _config = database
    document_id = uuid4()
    key = f"objects/{uuid4().hex}"
    data = (Path(__file__).parent / "fixtures" / "pdf_mixed.pdf").read_bytes()
    (storage / key).write_bytes(data)
    with factory.begin() as session:
        session.add(
            Document(
                id=document_id,
                kb_id=kb_id,
                file_name="mixed.pdf",
                file_sha256=hashlib.sha256(data).hexdigest(),
                storage_key=key,
            )
        )
    profile = EmbeddingProfile.fake()
    outcome = ingest_document(
        factory, document_id, storage, CheckedFake(engine), profile, uuid4()
    )
    assert outcome.status == "failed"
    assert outcome.error_code == "PARTIAL_PDF"
    with Session(engine) as session:
        assert session.get(Document, document_id).active_build_id is None
        assert (
            session.scalars(searchable_chunks_stmt(kb_id, profile.config_id)).all()
            == []
        )
