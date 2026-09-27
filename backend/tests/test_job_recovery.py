"""Fault injection uses a controllable clock and events, never lease-length sleeps."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from test_documents import document_env as document_env
from test_ingestion_jobs import job_env as job_env
from test_ingestion_jobs import upload

from alembic import command
from app.llm.contracts import ModelError
from app.llm.fake import FakeEmbeddingClient
from app.models import Chunk, Document, DocumentBuild, IngestionJob
from app.repositories.chunks import searchable_chunks_stmt
from app.services import ingestion_jobs as jobs
from app.services.ingest import EmbeddingProfile, IngestError


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds=60):
        self.value += timedelta(seconds=seconds)


class Crash(BaseException):
    """Simulate abrupt termination, bypassing ordinary error handlers."""


def prepared(env):
    response = upload(env)
    _, engine, storage, _, _, _, _, _, _ = env
    return response, sessionmaker(engine), storage, Clock()


def execute(factory, storage, claim, clock, client=None, fault=None):
    return jobs.execute_claim(
        factory,
        storage,
        lambda p: client or FakeEmbeddingClient(),
        claim,
        clock=clock,
        heartbeat_interval=None,
        fault_hook=fault,
    )


def test_crash_after_claim_reclaims_at_deadline_and_fences_old_owner(job_env):
    response, factory, storage, clock = prepared(job_env)
    first = jobs.claim_next(factory, clock=clock)
    assert first.attempts == 1
    assert jobs.claim_next(factory, clock=clock) is None
    clock.advance()
    with pytest.raises(IngestError, match="LEASE_LOST"):
        jobs.heartbeat(factory, first, clock=clock)
    second = jobs.claim_next(factory, clock=clock)
    assert first.id == second.id == UUID(response["job_id"])
    assert second.attempts == 2 and first.run_token != second.run_token
    fake = FakeEmbeddingClient()
    assert execute(factory, storage, first, clock, fake)["status"] == "lease_lost"
    assert fake.call_count == 0
    assert execute(factory, storage, second, clock, fake)["status"] == "succeeded"
    with factory() as session:
        job = session.get(IngestionJob, second.id)
        assert job.build_id == second.run_token and job.lease_expires_at is None


@pytest.mark.parametrize(
    "stage", ["after_embedding", "before_publish", "publish_transaction"]
)
def test_crashes_rebuild_without_duplicate_effective_chunks(job_env, stage):
    response, factory, storage, clock = prepared(job_env)
    first = jobs.claim_next(factory, clock=clock)
    fake = FakeEmbeddingClient()

    def fault(current):
        if current == stage:
            raise Crash()

    with pytest.raises(Crash):
        execute(factory, storage, first, clock, fake, fault)
    with factory() as session:
        job = session.get(IngestionJob, first.id)
        doc = session.get(Document, first.document_id)
        build = session.get(DocumentBuild, first.run_token)
        assert job.status == "running" and build.status == "processing"
        assert job.build_id == first.run_token and doc.active_build_id is None
    clock.advance()
    second = jobs.claim_next(factory, clock=clock)
    assert execute(factory, storage, second, clock, fake)["status"] == "succeeded"
    assert (
        fake.call_count == 2
    )  # External work is repeated: explicitly not exactly-once.
    with factory() as session:
        assert session.get(DocumentBuild, first.run_token).status == "failed"
        doc = session.get(Document, first.document_id)
        items = list(
            session.scalars(
                searchable_chunks_stmt(doc.kb_id, EmbeddingProfile.fake().config_id)
            )
        )
        assert len(items) == 1 and items[0].build_id == second.run_token
        assert (
            session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.build_id == second.run_token)
            )
            == 1
        )
    assert execute(factory, storage, second, clock, fake)["status"] == "lease_lost"
    assert fake.call_count == 2


@pytest.mark.parametrize("late_error", [False, True])
def test_old_worker_delayed_result_cannot_overwrite_new_success(job_env, late_error):
    _, factory, storage, clock = prepared(job_env)
    first = jobs.claim_next(factory, clock=clock)
    entered, release = Event(), Event()

    class DelayedFake(FakeEmbeddingClient):
        def embed_documents(self, texts):
            result = super().embed_documents(texts)
            entered.set()
            assert release.wait(5), "Test must release old model response"
            if late_error:
                raise ModelError("MODEL_AUTH_FAILED")
            return result

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute, factory, storage, first, clock, DelayedFake())
        try:
            assert entered.wait(5)
            clock.advance()
            second = jobs.claim_next(factory, clock=clock)
            assert execute(factory, storage, second, clock)["status"] == "succeeded"
        finally:
            release.set()
        assert future.result(timeout=5)["status"] == "lease_lost"
    with factory() as session:
        job = session.get(IngestionJob, first.id)
        assert job.status == "succeeded" and job.error_code is None
        assert job.build_id == second.run_token and job.run_token == second.run_token
        assert (
            session.get(Document, first.document_id).active_build_id == second.run_token
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.build_id == first.run_token)
            )
            == 0
        )


def test_heartbeat_extends_but_cannot_resurrect_or_renew_another_run(job_env):
    _, factory, _, clock = prepared(job_env)
    first = jobs.claim_next(factory, clock=clock)
    clock.advance(10)
    jobs.heartbeat(factory, first, clock=clock)
    with factory() as session:
        job = session.get(IngestionJob, first.id)
        assert (
            job.heartbeat_at == clock()
            and job.lease_expires_at == clock() + timedelta(seconds=60)
        )
    clock.advance(50)
    assert jobs.claim_next(factory, clock=clock) is None
    clock.advance(10)
    with pytest.raises(IngestError, match="LEASE_LOST"):
        jobs.heartbeat(factory, first, clock=clock)
    second = jobs.claim_next(factory, clock=clock)
    with pytest.raises(IngestError, match="LEASE_LOST"):
        jobs.heartbeat(factory, first, clock=clock)
    with factory() as session:
        assert session.get(IngestionJob, first.id).run_token == second.run_token


def test_background_heartbeat_uses_event_and_short_transaction(job_env, monkeypatch):
    _, factory, _, clock = prepared(job_env)
    claim = jobs.claim_next(factory, clock=clock)
    clock.advance(30)
    seen = Event()
    original = jobs.heartbeat

    def observed(*args, **kwargs):
        original(*args, **kwargs)
        seen.set()

    monkeypatch.setattr(jobs, "heartbeat", observed)
    with jobs.Heartbeat(factory, claim, interval=0.01, clock=clock):
        assert seen.wait(3)
    with factory() as session:
        job = session.get(IngestionJob, claim.id)
        assert job.heartbeat_at == clock()
        assert job.lease_expires_at == clock() + timedelta(seconds=60)


def test_crash_retry_budget_exhausted_without_fourth_claim(job_env):
    response, factory, _, clock = prepared(job_env)
    for attempt in range(1, 4):
        claim = jobs.claim_next(factory, clock=clock)
        assert claim.attempts == attempt
        clock.advance()
    assert jobs.claim_next(factory, clock=clock) is None
    with factory() as session:
        job = session.get(IngestionJob, UUID(response["job_id"]))
        assert job.status == "failed" and job.error_code == "RETRY_EXHAUSTED"
        assert job.attempts == job.max_attempts == 3
        assert session.scalar(select(func.count()).select_from(DocumentBuild)) == 0


def test_transient_errors_retry_only_three_times(job_env):
    _, factory, storage, clock = prepared(job_env)

    class TimeoutFake(FakeEmbeddingClient):
        def embed_documents(self, texts):
            self.call_count += 1
            raise ModelError("MODEL_TIMEOUT")

    fake = TimeoutFake()
    for attempt in range(1, 4):
        outcome = jobs.process_one(
            factory, storage, lambda p: fake, clock=clock, heartbeat_interval=None
        )
        assert outcome["attempts"] == attempt
        assert outcome["status"] == ("queued" if attempt < 3 else "failed")
        assert outcome["error_code"] == "MODEL_TIMEOUT"
    assert (
        jobs.process_one(
            factory, storage, lambda p: fake, clock=clock, heartbeat_interval=None
        )
        is None
    )
    assert fake.call_count == 3


@pytest.mark.parametrize(
    "code",
    ["MODEL_AUTH_FAILED", "EMBEDDING_DIMENSION_MISMATCH", "OPENAI_API_KEY_REQUIRED"],
)
def test_permanent_failure_does_not_retry(job_env, code):
    _, factory, storage, clock = prepared(job_env)

    def fail(profile):
        raise IngestError(code)

    outcome = jobs.process_one(
        factory, storage, fail, clock=clock, heartbeat_interval=None
    )
    assert outcome["status"] == "failed" and outcome["attempts"] == 1
    assert jobs.claim_next(factory, clock=clock) is None


def test_deleted_document_cannot_publish(job_env):
    _, factory, storage, clock = prepared(job_env)
    claim = jobs.claim_next(factory, clock=clock)

    def delete_before_publish(stage):
        if stage == "before_publish":
            with factory.begin() as session:
                doc = session.get(Document, claim.document_id, with_for_update=True)
                doc.deleted_at = clock()
                doc.active_build_id = None

    result = execute(factory, storage, claim, clock, fault=delete_before_publish)
    assert (
        result["status"] == "failed" and result["error_code"] == "BUILD_NOT_PUBLISHABLE"
    )
    with factory() as session:
        assert session.get(Document, claim.document_id).active_build_id is None
        assert session.get(DocumentBuild, claim.run_token).status == "failed"


def test_lease_expired_before_publish_without_new_owner_is_rejected(job_env):
    _, factory, storage, clock = prepared(job_env)
    claim = jobs.claim_next(factory, clock=clock)
    result = execute(
        factory,
        storage,
        claim,
        clock,
        fault=lambda stage: clock.advance() if stage == "before_publish" else None,
    )
    assert result["status"] == "lease_lost"
    with factory() as session:
        assert session.get(Document, claim.document_id).active_build_id is None
        assert session.get(IngestionJob, claim.id).status == "running"


def test_migration_keeps_legacy_job_and_reclaims_it(job_env):
    response, factory, storage, clock = prepared(job_env)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.downgrade(config, "0004_ingestion_jobs")
    from sqlalchemy import text

    with factory.begin() as session:
        session.execute(
            text("UPDATE ingestion_jobs SET status='running', attempts=1 WHERE id=:id"),
            {"id": response["job_id"]},
        )
        session.add(
            DocumentBuild(
                id=UUID(response["job_id"]),
                document_id=UUID(response["document_id"]),
                status="processing",
                parser_config={},
                chunking_config={},
                model_config_id="legacy",
            )
        )
    command.upgrade(config, "head")
    command.check(config)
    claim = jobs.claim_next(factory, clock=clock)
    assert claim.attempts == 2
    with factory() as session:
        assert session.get(DocumentBuild, UUID(response["job_id"])).status == "failed"
    assert execute(factory, storage, claim, clock)["status"] == "succeeded"
