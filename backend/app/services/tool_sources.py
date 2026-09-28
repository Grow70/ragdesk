"""Reauthorize a bounded batch of registered sources before exposing text."""

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from app.repositories.sources import Source, current_sources
from app.services.knowledge_bases import require_kb_member


@dataclass(frozen=True, slots=True)
class ChunkPermit:
    chunk_id: UUID
    document_id: UUID
    build_id: UUID
    content_hash: str
    rank: int
    distance: float | None

    @classmethod
    def from_retrieved(cls, chunk):
        return cls(
            chunk.chunk_id,
            chunk.document_id,
            chunk.build_id,
            sha256(chunk.text.encode("utf-8")).hexdigest(),
            chunk.rank,
            chunk.distance,
        )


class EvidenceExpired(Exception):
    pass


def read_permitted(factory, user_id, kb_id, permits: list[ChunkPermit]) -> list[Source]:
    if not 1 <= len(permits) <= 20:
        raise ValueError("Expected 1 to 20 registered chunks")
    with factory() as session:
        require_kb_member(session, user_id, kb_id)
        sources = current_sources(session, kb_id, [p.chunk_id for p in permits])
        ordered = []
        for permit in permits:
            source = sources.get(permit.chunk_id)
            if (
                source is None
                or source.document_id != permit.document_id
                or source.build_id != permit.build_id
                or sha256(source.snippet.encode("utf-8")).hexdigest()
                != permit.content_hash
            ):
                raise EvidenceExpired()
            ordered.append(source)
        return ordered
