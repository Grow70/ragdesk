"""Source reads constrained by knowledge base and the current published build."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chunk, Document, DocumentBuild


@dataclass(frozen=True, slots=True)
class Source:
    document_id: UUID
    build_id: UUID
    chunk_id: UUID
    document_name: str
    snippet: str
    page_number: int | None
    heading_path: list[str] | None
    start_line: int | None
    end_line: int | None


def current_sources(
    session: Session, kb_id: UUID, chunk_ids: list[UUID]
) -> dict[UUID, Source]:
    rows = session.execute(
        select(Chunk, Document.id, Document.file_name)
        .join(DocumentBuild, Chunk.build_id == DocumentBuild.id)
        .join(Document, DocumentBuild.document_id == Document.id)
        .where(
            Document.kb_id == kb_id,
            Document.deleted_at.is_(None),
            Document.active_build_id == DocumentBuild.id,
            DocumentBuild.status == "ready",
            Chunk.id.in_(chunk_ids),
        )
    )
    return {
        chunk.id: Source(
            document_id,
            chunk.build_id,
            chunk.id,
            name,
            chunk.body,
            chunk.page_number,
            chunk.heading_path,
            chunk.start_line,
            chunk.end_line,
        )
        for chunk, document_id, name in rows
    }
