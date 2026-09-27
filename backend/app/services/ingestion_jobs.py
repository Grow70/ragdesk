"""Durable jobs, renewable leases and token-fenced ingestion transactions."""

import math
import re
import threading
from dataclasses import asdict, dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, text

from app.models import Document, DocumentBuild, IngestionJob
from app.services.ingest import EmbeddingProfile, IngestError, ingest_document
from app.services.knowledge_bases import NotFound, require_kb_admin

ACTIVE = ("queued", "running")


def configured_profile(settings):
    if settings.retrieval_embedding_backend == "fake":
        return EmbeddingProfile.fake()
    return EmbeddingProfile(
        provider="openai",
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )


def enqueue(session, user_id, kb_id, document_id, profile, *, reuse_latest=False):
    """Participate in caller's transaction: never commit an upload separately."""
    require_kb_admin(session, user_id, kb_id)
    document = session.scalar(
        select(Document)
        .where(
            Document.id == document_id,
            Document.kb_id == kb_id,
            Document.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if document is None:
        raise NotFound()
    session.expire_all()  # Refresh roles if the document lock required waiting.
    require_kb_admin(session, user_id, kb_id)
    statement = select(IngestionJob).where(IngestionJob.document_id == document_id)
    active = session.scalar(statement.where(IngestionJob.status.in_(ACTIVE)))
    if active is not None:
        return active
    if reuse_latest:
        latest = session.scalar(
            statement.order_by(
                IngestionJob.created_at.desc(), IngestionJob.id.desc()
            ).limit(1)
        )
        if latest is not None:
            return latest
    job = IngestionJob(
        id=uuid4(),
        document_id=document_id,
        requested_by=user_id,
        status="queued",
        attempts=0,
        profile=asdict(profile),
    )
    session.add(job)
    session.flush()
    return job


def visible_job(session, user_id, kb_id, document_id, job_id):
    require_kb_admin(session, user_id, kb_id)
    job = session.scalar(
        select(IngestionJob)
        .join(Document, Document.id == IngestionJob.document_id)
        .where(
            IngestionJob.id == job_id,
            Document.id == document_id,
            Document.kb_id == kb_id,
            Document.deleted_at.is_(None),
        )
    )
    if job is None:
        raise NotFound()
    return job


def document_status(session, document):
    if document.active_build_id is not None:
        return "ready"
    latest = session.scalar(
        select(IngestionJob.status)
        .where(IngestionJob.document_id == document.id)
        .order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
        .limit(1)
    )
    return {"queued": "queued", "running": "processing", "failed": "failed"}.get(
        latest, "uploaded"
    )


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    document_id: UUID
    profile: dict
    run_token: UUID
    attempts: int
    lease_seconds: float


def _now(session, clock=None):
    now = (
        clock() if clock is not None else session.scalar(select(func.clock_timestamp()))
    )
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Clock must return a timezone-aware datetime")
    return now


def _lease_seconds(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("lease_seconds must be finite and positive")
    return value


def _abandon_build(session, job, now, code):
    # Legacy 20A jobs used job.id, and could crash before setting job.build_id.
    build = session.get(
        DocumentBuild, job.build_id or job.run_token or job.id, with_for_update=True
    )
    if (
        build is not None
        and build.document_id == job.document_id
        and build.status == "processing"
    ):
        build.status = "failed"
        build.error_code = build.error_message = code
        build.finished_at = now


def claim_next(factory, *, clock=None, lease_seconds=60):
    _lease_seconds(lease_seconds)
    while True:
        with factory.begin() as session:
            now = _now(session, clock)
            job = session.scalar(
                select(IngestionJob)
                .where(
                    or_(
                        IngestionJob.status == "queued",
                        and_(
                            IngestionJob.status == "running",
                            or_(
                                IngestionJob.lease_expires_at <= now,
                                IngestionJob.lease_expires_at.is_(None),
                            ),
                        ),
                    )
                )
                .order_by(IngestionJob.created_at, IngestionJob.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            now = _now(session, clock)
            if job.status == "running":
                _abandon_build(session, job, now, "LEASE_EXPIRED")
            if job.attempts >= job.max_attempts:
                job.status = "failed"
                job.error_code = job.error_summary = "RETRY_EXHAUSTED"
                job.finished_at = now
                job.lease_expires_at = None
                continue
            job.status = "running"
            job.attempts += 1
            job.run_token = uuid4()
            job.build_id = None
            job.started_at = job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            job.finished_at = None
            job.error_code = job.error_summary = None
            return ClaimedJob(
                job.id,
                job.document_id,
                dict(job.profile),
                job.run_token,
                job.attempts,
                lease_seconds,
            )


RETRYABLE = {"MODEL_TIMEOUT", "MODEL_NETWORK_ERROR", "MODEL_UNAVAILABLE"}


class LeaseGuard:
    """All checks use the caller's write transaction, never a separate preflight."""

    def __init__(self, claimed, clock=None):
        self.claimed = claimed
        self.clock = clock

    def check(self, session):
        job = session.get(
            IngestionJob, self.claimed.id, with_for_update=True, populate_existing=True
        )
        now = _now(session, self.clock)
        if (
            job is None
            or job.status != "running"
            or job.run_token != self.claimed.run_token
            or job.document_id != self.claimed.document_id
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            raise IngestError("LEASE_LOST")
        return job

    def bind(self, session, build_id):
        job = self.check(session)
        if build_id != self.claimed.run_token:
            raise IngestError("BUILD_CONFIG_MISMATCH")
        job.build_id = build_id

    def succeed(self, session):
        job = self.check(session)
        if job.build_id != self.claimed.run_token:
            raise IngestError("BUILD_CONFIG_MISMATCH")
        job.status = "succeeded"
        job.error_code = job.error_summary = None
        job.finished_at = _now(session, self.clock)
        job.lease_expires_at = None

    def fail(self, session, code):
        job = self.check(session)
        now = _now(session, self.clock)
        _abandon_build(session, job, now, code)
        job.status = (
            "queued"
            if code in RETRYABLE and job.attempts < job.max_attempts
            else "failed"
        )
        job.error_code = job.error_summary = code
        job.finished_at = now if job.status == "failed" else None
        job.lease_expires_at = None
        return _result(job)


def heartbeat(factory, claimed, *, clock=None):
    with factory.begin() as session:
        session.execute(text("SET LOCAL lock_timeout = '2s'"))
        job = LeaseGuard(claimed, clock).check(session)
        now = _now(session, clock)
        if job.lease_expires_at <= now:
            raise IngestError("LEASE_LOST")
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=claimed.lease_seconds)


class Heartbeat:
    def __init__(self, factory, claimed, interval=10, clock=None):
        if interval is not None and (
            not math.isfinite(interval) or not 0 < interval < claimed.lease_seconds
        ):
            raise ValueError("heartbeat interval must be positive and less than lease")
        self.factory, self.claimed, self.interval, self.clock = (
            factory,
            claimed,
            interval,
            clock,
        )
        self.stop = threading.Event()
        self.thread = None

    def __enter__(self):
        if self.interval is not None:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        return self

    def _run(self):
        while not self.stop.wait(self.interval):
            try:
                heartbeat(self.factory, self.claimed, clock=self.clock)
            except Exception:
                # No blind renewal after DB/lease failure. Publication still checks DB.
                return

    def __exit__(self, *_):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=3)


def _safe_error(exc):
    from app.llm.contracts import ModelError

    if isinstance(exc, (IngestError, ModelError)):
        code = exc.code
        if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", code):
            return code
    return "INGESTION_JOB_FAILED"


def _result(job):
    return {
        "job_id": str(job.id),
        "build_id": str(job.build_id) if job.build_id else None,
        "status": job.status,
        "attempts": job.attempts,
        "error_code": job.error_code,
    }


def execute_claim(
    factory,
    storage_dir,
    client_factory,
    claimed,
    *,
    clock=None,
    heartbeat_interval=10,
    fault_hook=None,
):
    guard = LeaseGuard(claimed, clock)
    client = None
    try:
        with Heartbeat(factory, claimed, heartbeat_interval, clock):
            with factory.begin() as session:
                guard.check(session)
            profile = EmbeddingProfile(**claimed.profile)
            client = client_factory(profile)
            ingest_document(
                factory,
                claimed.document_id,
                storage_dir,
                client,
                profile,
                build_id=claimed.run_token,
                execution=guard,
                fault_hook=fault_hook,
            )
        # Success/failure was already committed atomically by the ingestion service.
        with factory() as session:
            job = session.get(IngestionJob, claimed.id)
            if job.run_token != claimed.run_token:
                raise IngestError("LEASE_LOST")
            return _result(job)
    except Exception as exc:
        code = _safe_error(exc)
        if code != "LEASE_LOST":
            try:
                with factory.begin() as session:
                    return guard.fail(session, code)
            except IngestError as lost:
                if lost.code != "LEASE_LOST":
                    raise
        return {
            "job_id": str(claimed.id),
            "status": "lease_lost",
            "attempts": claimed.attempts,
            "error_code": "LEASE_LOST",
        }
    finally:
        if client is not None and hasattr(client, "close"):
            client.close()


def process_one(
    factory,
    storage_dir,
    client_factory,
    *,
    clock=None,
    lease_seconds=60,
    heartbeat_interval=10,
    fault_hook=None,
):
    if heartbeat_interval is not None and not 0 < heartbeat_interval < lease_seconds:
        raise ValueError("heartbeat interval must be positive and less than lease")
    claimed = claim_next(factory, clock=clock, lease_seconds=lease_seconds)
    if claimed is None:
        return None
    return execute_claim(
        factory,
        storage_dir,
        client_factory,
        claimed,
        clock=clock,
        heartbeat_interval=heartbeat_interval,
        fault_hook=fault_hook,
    )
