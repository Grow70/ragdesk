"""Rank-only, deterministic reciprocal rank fusion over two capped lists."""

from dataclasses import replace
from fractions import Fraction

from app.retrieval.vector import RetrievedChunk

RRF_K = 60
CANDIDATE_LIMIT = 20
_SOURCE_FIELDS = (
    "document_id",
    "build_id",
    "knowledge_base_id",
    "document_name",
    "text",
    "page_number",
    "heading_path",
)


def _unique(items):
    result = {}
    for item in items[:CANDIDATE_LIMIT]:
        if type(item.rank) is not int or not 1 <= item.rank <= CANDIDATE_LIMIT:
            raise ValueError("INVALID_RRF_RANK")
        previous = result.get(item.chunk_id)
        if previous is not None:
            _same_source(previous, item)
        if previous is None or item.rank < previous.rank:
            result[item.chunk_id] = item
    return result


def _same_source(left, right):
    if any(getattr(left, key) != getattr(right, key) for key in _SOURCE_FIELDS):
        raise ValueError("RRF_SOURCE_MISMATCH")


def fuse(
    vector: list[RetrievedChunk], bm25: list[RetrievedChunk], top_k: int = 5
) -> list[RetrievedChunk]:
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError("INVALID_TOP_K")
    left, right = _unique(vector), _unique(bm25)
    candidates, scores = {}, {}
    for chunk_id in left.keys() | right.keys():
        v, b = left.get(chunk_id), right.get(chunk_id)
        if v is not None and b is not None:
            _same_source(v, b)
        score = (Fraction(1, RRF_K + v.rank) if v else Fraction()) + (
            Fraction(1, RRF_K + b.rank) if b else Fraction()
        )
        scores[chunk_id] = score
        candidates[chunk_id] = replace(
            v or b,
            distance=v.distance if v else None,
            bm25_score=b.bm25_score if b else None,
            vector_rank=v.rank if v else None,
            bm25_rank=b.rank if b else None,
            rrf_score=float(score),
        )
    ordered = sorted(candidates, key=lambda key: (-scores[key], str(key)))
    return [
        replace(candidates[key], rank=rank)
        for rank, key in enumerate(ordered[:top_k], 1)
    ]
