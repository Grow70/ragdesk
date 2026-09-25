"""Candidate chunk writes and active-build scoped reads."""

from dataclasses import asdict
from uuid import UUID

from sqlalchemy import Select, func, null, select
from sqlalchemy.orm import Session

from app.chunking import ChunkDraft
from app.models import Chunk, Document, DocumentBuild, KnowledgeBase


def searchable_chunks_stmt(kb_id: UUID, model_config_id: str) -> Select[tuple[Chunk]]:
    """Return ready chunks from active builds in one non-deleted knowledge base.

    The caller must first authorize the backend-identified user for this kb_id.
    """
    return (
        select(Chunk)
        .join(DocumentBuild, Chunk.build_id == DocumentBuild.id)
        .join(Document, DocumentBuild.document_id == Document.id)
        .where(
            Document.kb_id == kb_id,
            Document.deleted_at.is_(None),
            Document.active_build_id == DocumentBuild.id,
            DocumentBuild.status == "ready",
            DocumentBuild.model_config_id == model_config_id,
            Chunk.embedding.is_not(None),
        )
        .order_by(Document.id, Chunk.ordinal)
    )


def locked_document(session: Session, document_id: UUID) -> Document | None:
    return session.scalar(
        select(Document).where(Document.id == document_id).with_for_update()
    )


def locked_kb(session: Session, kb_id: UUID) -> KnowledgeBase | None:
    return session.scalar(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id).with_for_update()
    )


def processing_build_exists(session: Session, document_id: UUID) -> bool:
    return (
        session.scalar(
            select(DocumentBuild.id).where(
                DocumentBuild.document_id == document_id,
                DocumentBuild.status == "processing",
            )
        )
        is not None
    )


def active_other_config_ids(
    session: Session, kb_id: UUID, document_id: UUID
) -> set[str]:
    return set(
        session.scalars(
            select(DocumentBuild.model_config_id)
            .join(Document, Document.active_build_id == DocumentBuild.id)
            .where(
                Document.kb_id == kb_id,
                Document.id != document_id,
                Document.deleted_at.is_(None),
                DocumentBuild.status == "ready",
            )
        )
    )


def insert_candidates(
    session: Session,
    build_id: UUID,
    drafts: list[ChunkDraft],
    vectors: list[list[float]],
) -> None:
    session.add_all(
        Chunk(
            build_id=build_id,
            ordinal=draft.ordinal,
            body=draft.text,
            content_sha256=draft.content_sha256,
            page_number=draft.page_number,
            heading_path=draft.heading_path
            if draft.heading_path is not None
            else null(),
            start_line=draft.start_line,
            end_line=draft.end_line,
            source_spans=[asdict(span) for span in draft.source_spans],
            embedding=vector,
        )
        for draft, vector in zip(drafts, vectors, strict=True)
    )


def candidate_totals(
    session: Session, build_id: UUID
) -> tuple[int, int | None, int | None, int]:
    count, low, high, missing = session.execute(
        select(
            func.count(Chunk.id),
            func.min(Chunk.ordinal),
            func.max(Chunk.ordinal),
            func.count(Chunk.id).filter(Chunk.embedding.is_(None)),
        ).where(Chunk.build_id == build_id)
    ).one()
    return count, low, high, missing
