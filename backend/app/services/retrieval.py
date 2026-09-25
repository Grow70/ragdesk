"""Authorize, embed, and retrieve with no database transaction across model calls."""

import math
from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.llm.contracts import EmbeddingClient, ModelError
from app.retrieval.vector import RetrievedChunk, cosine_search, has_candidates
from app.services.ingest import EmbeddingProfile
from app.services.knowledge_bases import require_kb_member


class RetrievalError(Exception):
    def __init__(self, code: str, status: int, message: str):
        self.code = code
        self.status = status
        self.message = message
        super().__init__(code)


def _query_vector(client: EmbeddingClient, profile: EmbeddingProfile, query: str):
    if (
        client.provider != profile.provider
        or client.model != profile.model
        or client.dimensions != profile.dimensions
    ):
        raise RetrievalError(
            "EMBEDDING_CONFIG_MISMATCH", 503, "Embedding configuration mismatch"
        )
    try:
        vectors = client.embed_query(query).vectors
    except ModelError as exc:
        status = 504 if exc.code == "MODEL_TIMEOUT" else 502
        raise RetrievalError(exc.code, status, "Embedding service failed") from None
    if not isinstance(vectors, list) or len(vectors) != 1:
        raise RetrievalError("MODEL_INVALID_VECTOR", 502, "Invalid embedding result")
    vector = vectors[0]
    try:
        finite = isinstance(vector, list) and all(
            type(value) in (int, float) and math.isfinite(value) for value in vector
        )
    except OverflowError:
        finite = False
    if (
        not isinstance(vector, list)
        or len(vector) != profile.dimensions
        or not finite
        or not any(value != 0 for value in vector)
    ):
        raise RetrievalError("MODEL_INVALID_VECTOR", 502, "Invalid embedding result")
    return vector


def search(
    factory: sessionmaker[Session],
    user_id: UUID,
    kb_id: UUID,
    query: str,
    top_k: int,
    profile: EmbeddingProfile,
    client_factory: Callable[[], EmbeddingClient],
) -> list[RetrievedChunk]:
    with factory() as session:
        require_kb_member(session, user_id, kb_id)
        if not has_candidates(session, kb_id, profile.config_id):
            return []

    # The candidate check's transaction is closed before any network work.
    client = client_factory()
    try:
        vector = _query_vector(client, profile, query)
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            close()

    with factory() as session:
        # A member removed during embedding cannot receive results.
        require_kb_member(session, user_id, kb_id)
        return cosine_search(session, kb_id, profile.config_id, vector, top_k)
