"""Durable enqueue and short claim/finalize transactions; no crash recovery yet."""

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select

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


def claim_next(factory):
    with factory.begin() as session:
        job = session.scalar(
            select(IngestionJob)
            .where(IngestionJob.status == "queued")
            .order_by(IngestionJob.created_at, IngestionJob.id)
            .limit(1)
            .with_for_update()
        )
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.started_at = datetime.now(timezone.utc)
        return ClaimedJob(job.id, job.document_id, dict(job.profile))


def _safe_error(exc):
    # Typed errors contain machine codes; arbitrary exception messages never persist.
    from app.llm.contracts import ModelError

    if isinstance(exc, (IngestError, ModelError)):
        code = exc.code
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", code):
            return code
    return "INGESTION_JOB_FAILED"


def process_one(factory, storage_dir, client_factory):
    """Single worker normal flow. A process/DB crash deliberately leaves running."""
    claimed = claim_next(factory)
    if claimed is None:
        return None
    error_code = None
    client = None
    try:
        profile = EmbeddingProfile(**claimed.profile)
        client = client_factory(profile)
        outcome = ingest_document(
            factory,
            claimed.document_id,
            storage_dir,
            client,
            profile,
            build_id=claimed.id,
        )
        if outcome.status != "ready":
            raise IngestError(outcome.error_code or "BUILD_NOT_READY")
    except Exception as exc:
        error_code = _safe_error(exc)
    finally:
        if client is not None and hasattr(client, "close"):
            client.close()
    with factory.begin() as session:
        job = session.get(IngestionJob, claimed.id, with_for_update=True)
        if job is None or job.status != "running":
            raise IngestError("JOB_NOT_RUNNING")
        build = session.get(DocumentBuild, claimed.id)
        job.build_id = build.id if build is not None else None
        job.status = "succeeded" if error_code is None else "failed"
        job.error_code = error_code
        job.error_summary = error_code
        job.finished_at = datetime.now(timezone.utc)
        return {
            "job_id": str(job.id),
            "build_id": str(job.build_id) if job.build_id else None,
            "status": job.status,
            "attempts": job.attempts,
            "error_code": error_code,
        }
