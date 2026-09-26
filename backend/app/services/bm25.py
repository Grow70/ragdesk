"""Read authorized active chunks, build BM25, and revalidate before returning."""

from time import perf_counter
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.repositories.chunks import lexical_chunks_stmt
from app.retrieval.bm25 import rank_chunks
from app.retrieval.vector import RetrievedChunk
from app.services.knowledge_bases import require_kb_member
from app.services.retrieval import RetrievalError


def read_corpus(factory, user_id: UUID, kb_id: UUID) -> list[dict]:
    with factory() as session:
        require_kb_member(session, user_id, kb_id)
        return [
            dict(row)
            for row in session.execute(lexical_chunks_stmt(user_id, kb_id)).mappings()
        ]


def search(
    factory: sessionmaker[Session],
    user_id: UUID,
    kb_id: UUID,
    query: str,
    top_k: int = 5,
    *,
    trace: dict | None = None,
) -> list[RetrievedChunk]:
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise RetrievalError("INVALID_TOP_K", 422, "top_k must be between 1 and 20")
    if not isinstance(query, str) or not query.strip() or len(query) > 4000:
        raise RetrievalError(
            "INVALID_QUERY", 422, "query must contain 1 to 4000 characters"
        )
    total_start = perf_counter()
    stats = {}
    try:
        start = perf_counter()
        corpus = read_corpus(factory, user_id, kb_id)
        stats["load_ms"] = (perf_counter() - start) * 1000
        chunks = [
            RetrievedChunk(
                **{
                    key: row[key]
                    for key in (
                        "chunk_id",
                        "document_id",
                        "build_id",
                        "knowledge_base_id",
                        "document_name",
                        "text",
                        "page_number",
                        "heading_path",
                    )
                },
                distance=None,
                rank=0,
            )
            for row in corpus
        ]
        result = rank_chunks(chunks, query, top_k, stats)
        start = perf_counter()
        current = read_corpus(factory, user_id, kb_id)
        stats["revalidate_ms"] = (perf_counter() - start) * 1000
        if current != corpus:
            raise RetrievalError("BM25_CORPUS_CHANGED", 409, "Retry on current corpus")
        if trace is not None:
            trace["corpus"] = corpus
        return result
    finally:
        stats["total_ms"] = (perf_counter() - total_start) * 1000
        if trace is not None:
            trace["cost"] = stats
