"""Normal task flow against temporary PostgreSQL; all model work is explicit fake."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from test_documents import _headers, _upload
from test_documents import document_env as document_env

from alembic import command
from app import worker
from app.config import load_settings
from app.llm.contracts import ModelError
from app.llm.fake import FakeEmbeddingClient
from app.main import create_app
from app.models import Document, DocumentBuild, IngestionJob
from app.repositories.chunks import searchable_chunks_stmt
from app.services import ingestion_jobs as jobs
from app.services.ingest import EmbeddingProfile


@pytest.fixture
def job_env(document_env, monkeypatch):
    engine, storage, users = document_env
    monkeypatch.setenv("RETRIEVAL_EMBEDDING_BACKEND", "fake")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    alice, bob, carol = (_headers(users[n]) for n in ("alice", "bob", "carol"))
    with TestClient(create_app()) as client:
        a = client.post("/knowledge-bases", json={"name": "A"}, headers=alice).json()[
            "id"
        ]
        b = client.post("/knowledge-bases", json={"name": "B"}, headers=bob).json()[
            "id"
        ]
        assert (
            client.put(
                f"/knowledge-bases/{a}/members/{users['carol']}",
                json={"role": "member"},
                headers=alice,
            ).status_code
            == 200
        )
        yield client, engine, storage, users, a, b, alice, bob, carol


def upload(env, data=b"Demo: limit 680 CNY."):
    client, _, _, _, a, _, alice, _, _ = env
    response = _upload(client, a, alice, "demo.txt", data)
    assert response.status_code == 202, response.text
    return response.json()


def test_upload_queues_without_model_then_worker_publishes(job_env, monkeypatch):
    client, engine, storage, users, a, b, alice, bob, carol = job_env

    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP upload must not ingest or call models")

    original = jobs.ingest_document
    monkeypatch.setattr(jobs, "ingest_document", forbidden)
    start = perf_counter()
    response = upload(job_env)
    elapsed = (perf_counter() - start) * 1000
    print(f"upload_normal_flow_ms={elapsed:.3f}; n=1; no worker/model in request")
    monkeypatch.setattr(jobs, "ingest_document", original)
    assert response["job_status"] == "queued"
    assert client.get(response["status_url"], headers=alice).json()["attempts"] == 0
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DocumentBuild)) == 0
        assert (
            session.get(Document, UUID(response["document_id"])).active_build_id is None
        )
    repeated = upload(job_env)
    explicit = client.post(
        f"/knowledge-bases/{a}/documents/{response['document_id']}/ingestions",
        headers=alice,
    )
    assert repeated["job_id"] == explicit.json()["job_id"] == response["job_id"]
    assert explicit.status_code == 202
    assert client.get(response["status_url"], headers=bob).status_code == 404
    assert client.get(response["status_url"], headers=carol).status_code == 403
    assert (
        client.get(response["status_url"].replace(a, b), headers=bob).status_code == 404
    )
    assert (
        client.get(
            response["status_url"].replace(response["document_id"], str(uuid4())),
            headers=alice,
        ).status_code
        == 404
    )
    assert (
        client.get(
            response["status_url"].replace(response["job_id"], str(uuid4())),
            headers=alice,
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/knowledge-bases/{a}/documents/{response['document_id']}/ingestions",
            headers=carol,
        ).status_code
        == 403
    )
    factory = sessionmaker(engine)

    class CheckedFake(FakeEmbeddingClient):
        def embed_documents(self, texts):
            assert engine.pool.checkedout() == 0
            with factory.begin() as session:
                job = session.scalar(
                    select(IngestionJob)
                    .where(IngestionJob.id == UUID(response["job_id"]))
                    .with_for_update(nowait=True)
                )
                assert job.status == "running" and job.attempts == 1
                doc = session.scalar(
                    select(Document)
                    .where(Document.id == job.document_id)
                    .with_for_update(nowait=True)
                )
                build = session.scalar(
                    select(DocumentBuild)
                    .where(DocumentBuild.id == job.id)
                    .with_for_update(nowait=True)
                )
                assert doc.active_build_id is None and build.status == "processing"
            return super().embed_documents(texts)

    fake = CheckedFake()
    result = jobs.process_one(factory, storage, lambda profile: fake)
    assert result["status"] == "succeeded" and fake.call_count == 1
    status = client.get(response["status_url"], headers=alice).json()
    assert status["status"] == "succeeded" and status["build_id"] == response["job_id"]
    assert status["attempts"] == 1 and status["error_summary"] is None
    assert status["started_at"] and status["finished_at"]
    with Session(engine) as session:
        assert session.get(
            Document, UUID(response["document_id"])
        ).active_build_id == UUID(status["build_id"])
        assert (
            len(
                list(
                    session.scalars(
                        searchable_chunks_stmt(
                            UUID(a), EmbeddingProfile.fake().config_id
                        )
                    )
                )
            )
            == 1
        )
    assert jobs.process_one(factory, storage, lambda profile: fake) is None
    assert (
        upload(job_env)["job_id"] == response["job_id"]
    )  # duplicate terminal: no rebuild
    assert (
        client.get(
            f"/knowledge-bases/{a}/documents/{response['document_id']}", headers=alice
        ).json()["status"]
        == "ready"
    )
    # Revoking a previously authorized administrator invalidates the next status read.
    assert (
        client.put(
            f"/knowledge-bases/{a}/members/{users['carol']}",
            json={"role": "admin"},
            headers=alice,
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/knowledge-bases/{a}/members/{users['alice']}", headers=carol
        ).status_code
        == 200
    )
    assert client.get(response["status_url"], headers=alice).status_code == 404


def test_failed_rebuild_preserves_active_and_explicit_retry_is_new_job(job_env):
    client, engine, storage, _, a, _, alice, _, _ = job_env
    response = upload(job_env)
    factory = sessionmaker(engine)
    first = jobs.process_one(factory, storage, lambda p: FakeEmbeddingClient())
    url = f"/knowledge-bases/{a}/documents/{response['document_id']}/ingestions"
    rebuild = client.post(url, headers=alice).json()

    class TimeoutFake(FakeEmbeddingClient):
        def embed_documents(self, texts):
            raise ModelError("MODEL_TIMEOUT")

    failed = jobs.process_one(factory, storage, lambda p: TimeoutFake())
    assert failed["status"] == "failed" and failed["error_code"] == "MODEL_TIMEOUT"
    with Session(engine) as session:
        assert session.get(
            Document, UUID(response["document_id"])
        ).active_build_id == UUID(first["build_id"])
        assert session.get(DocumentBuild, UUID(failed["build_id"])).status == "failed"
    assert upload(job_env)["job_id"] == rebuild["job_id"]
    retry = client.post(url, headers=alice).json()
    assert retry["job_id"] != rebuild["job_id"]
    assert (
        jobs.process_one(factory, storage, lambda p: FakeEmbeddingClient())["status"]
        == "succeeded"
    )


def test_concurrent_enqueue_and_database_constraints(job_env):
    _, engine, storage, users, a, _, _, _, _ = job_env
    response = upload(job_env)
    document_id = UUID(response["document_id"])
    factory = sessionmaker(engine)
    assert (
        jobs.process_one(factory, storage, lambda p: FakeEmbeddingClient())["status"]
        == "succeeded"
    )

    def request_job(_):
        with factory.begin() as session:
            return jobs.enqueue(
                session, users["alice"], UUID(a), document_id, EmbeddingProfile.fake()
            ).id

    with ThreadPoolExecutor(max_workers=4) as executor:
        created_ids = set(executor.map(request_job, range(8)))
    assert len(created_ids) == 1
    created_id = created_ids.pop()
    assert created_id != UUID(response["job_id"])
    base = dict(
        document_id=document_id,
        requested_by=users["alice"],
        status="queued",
        attempts=0,
        profile=asdict(EmbeddingProfile.fake()),
    )
    failed = {"status": "failed", "error_code": "TEST", "error_summary": "TEST"}
    for changes, constraint in (
        ({}, "uq_ingestion_jobs_active_document"),
        (failed | {"document_id": uuid4()}, "ingestion_jobs_document_id_fkey"),
        (failed | {"requested_by": uuid4()}, "ingestion_jobs_requested_by_fkey"),
        (failed | {"attempts": -1}, "ck_ingestion_jobs_attempts"),
        ({"status": "failed"}, "ck_ingestion_jobs_failed_error"),
        (
            {"status": "succeeded", "build_id": uuid4()},
            "fk_ingestion_jobs_build_same_document",
        ),
    ):
        with pytest.raises(IntegrityError) as caught:
            with factory.begin() as session:
                session.add(IngestionJob(id=uuid4(), **(base | changes)))
        assert caught.value.orig.diag.constraint_name == constraint
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 2
    assert jobs.claim_next(factory).id == created_id
    assert jobs.claim_next(factory) is None  # no re-claim/recovery implemented
    assert request_job(None) == created_id


def test_enqueue_failure_rolls_back_document_job_and_file(job_env, monkeypatch):
    client, engine, storage, _, a, _, alice, _, _ = job_env
    from app.services import documents

    original = documents.enqueue

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("private-sentinel")

    monkeypatch.setattr(documents, "enqueue", fail)
    result = _upload(client, a, alice, "demo.txt", b"demo")
    assert result.status_code == 500 and "private-sentinel" not in result.text
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
    assert not list((storage / "staging").iterdir())
    assert not list((storage / "objects").iterdir())


def test_independent_worker_cli_process_and_idle(job_env):
    client, _, _, _, _, _, alice, _, _ = job_env
    response = upload(job_env)
    for expected in ("succeeded", "idle"):
        result = subprocess.run(
            [sys.executable, "-m", "app.worker", "--once"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        assert output["status"] == expected
    assert (
        client.get(response["status_url"], headers=alice).json()["status"]
        == "succeeded"
    )


def test_missing_real_key_fails_without_fake_and_safe_exception(job_env):
    _, engine, storage, _, _, _, _, _, _ = job_env
    response = upload(job_env)
    with Session(engine) as session:
        session.get(IngestionJob, UUID(response["job_id"])).profile = asdict(
            EmbeddingProfile("openai", "text-embedding-3-small")
        )
        session.commit()
    settings = load_settings()
    result = jobs.process_one(
        sessionmaker(engine), storage, lambda p: worker.embedding_client(settings, p)
    )
    assert (
        result["status"] == "failed"
        and result["error_code"] == "OPENAI_API_KEY_REQUIRED"
    )
    assert result["build_id"] is None
    second = upload(job_env, b"another document")

    def fail(profile):
        raise RuntimeError("provider-secret-sentinel")

    result = jobs.process_one(sessionmaker(engine), storage, fail)
    assert result["error_code"] == "INGESTION_JOB_FAILED"
    with Session(engine) as session:
        job = session.get(IngestionJob, UUID(second["job_id"]))
        assert job.error_summary == "INGESTION_JOB_FAILED"


def test_parser_failure_and_next_job_continues(job_env):
    _, engine, storage, _, _, _, _, _, _ = job_env
    upload(job_env, b"   \n\t")
    fake = FakeEmbeddingClient()
    failed = jobs.process_one(sessionmaker(engine), storage, lambda p: fake)
    assert failed["status"] == "failed" and fake.call_count == 0
    assert failed["build_id"] is not None
    upload(job_env, b"valid next job")
    assert (
        jobs.process_one(sessionmaker(engine), storage, lambda p: fake)["status"]
        == "succeeded"
    )


def test_migration_roundtrip_only_in_disposable_database(job_env):
    _, engine, _, _, _, _, _, _, _ = job_env
    response = upload(job_env)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.check(config)
    command.downgrade(config, "0003_ingest_vectors")
    assert "ingestion_jobs" not in inspect(engine).get_table_names()
    with Session(engine) as session:
        assert session.get(Document, UUID(response["document_id"])) is not None
    command.upgrade(config, "head")
    command.check(config)
    assert "ingestion_jobs" in inspect(engine).get_table_names()
