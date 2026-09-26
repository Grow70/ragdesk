"""Exact pgvector cosine search over one authorized knowledge base."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chunk, Document, DocumentBuild
from app.repositories.chunks import searchable_chunks_stmt


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: UUID
    document_id: UUID
    build_id: UUID
    knowledge_base_id: UUID
    document_name: str
    text: str
    page_number: int | None
    heading_path: list[str] | None
    distance: float | None
    rank: int
    bm25_score: float | None = None
    rrf_score: float | None = None
    vector_rank: int | None = None
    bm25_rank: int | None = None
    rrf_rank: int | None = None
    rerank_score: float | None = None


def has_candidates(session: Session, kb_id: UUID, model_config_id: str) -> bool:
    statement = (
        searchable_chunks_stmt(kb_id, model_config_id)
        .with_only_columns(Chunk.id)
        .order_by(None)
        .limit(1)
    )
    return session.scalar(statement) is not None


def cosine_search(
    session: Session,
    kb_id: UUID,
    model_config_id: str,
    query_vector: list[float],
    top_k: int,
) -> list[RetrievedChunk]:
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    statement = (
        select(Chunk, Document.id, Document.file_name, distance)
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
        .order_by(distance.asc(), Chunk.id.asc())
        .limit(top_k)
    )
    return [
        RetrievedChunk(
            chunk_id=chunk.id,
            document_id=document_id,
            build_id=chunk.build_id,
            knowledge_base_id=kb_id,
            document_name=file_name,
            text=chunk.body,
            page_number=chunk.page_number,
            heading_path=chunk.heading_path,
            distance=float(value),
            rank=rank,
        )
        for rank, (chunk, document_id, file_name, value) in enumerate(
            session.execute(statement), 1
        )
    ]
