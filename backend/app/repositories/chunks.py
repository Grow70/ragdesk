"""The only initial read path for searchable chunks."""

from uuid import UUID

from sqlalchemy import Select, select

from app.models import Chunk, Document, DocumentBuild


def searchable_chunks_stmt(kb_id: UUID) -> Select[tuple[Chunk]]:
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
        )
        .order_by(Document.id, Chunk.ordinal)
    )
