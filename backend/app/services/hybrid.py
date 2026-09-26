"""Authorize both routes, selectively degrade, revalidate, then fuse ranks."""

from dataclasses import asdict, dataclass
from time import perf_counter

from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.retrieval.rrf import CANDIDATE_LIMIT, RRF_K, fuse
from app.retrieval.vector import RetrievedChunk
from app.services import bm25, retrieval
from app.services.retrieval import RetrievalError


@dataclass(frozen=True)
class FusionConfig:
    allow_degraded: bool = False

    def __post_init__(self):
        if type(self.allow_degraded) is not bool:
            raise ValueError("allow_degraded must be boolean")


@dataclass(frozen=True)
class FusionResult:
    items: list[RetrievedChunk]
    trace: dict


def _transient_code(exc):
    if (
        isinstance(exc, RetrievalError)
        and exc.code in {"MODEL_TIMEOUT", "MODEL_NETWORK_ERROR", "MODEL_UNAVAILABLE"}
        and exc.status in (502, 503, 504)
    ):
        return exc.code
    if isinstance(exc, PoolTimeout):
        return "DATABASE_POOL_TIMEOUT"
    if isinstance(exc, DBAPIError):
        if getattr(exc.orig, "sqlstate", None) in {"40001", "40P01", "55P03", "57014"}:
            return "DATABASE_TRANSIENT"
    return None


def search(
    factory,
    user_id,
    kb_id,
    query,
    profile,
    embedding_factory,
    top_k=5,
    *,
    config=FusionConfig(),
    trace=None,
) -> FusionResult:
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise RetrievalError("INVALID_TOP_K", 422, "top_k must be between 1 and 20")
    if not isinstance(query, str) or not query.strip() or len(query) > 4000:
        raise RetrievalError(
            "INVALID_QUERY", 422, "query must contain 1 to 4000 characters"
        )
    trace = trace if trace is not None else {}
    trace.update(
        status="running",
        candidate_limit=CANDIDATE_LIMIT,
        rrf_k=RRF_K,
        top_k=top_k,
        allow_degraded=config.allow_degraded,
        degraded=False,
        routes={
            name: {"status": "not_run", "items": []} for name in ("vector", "bm25")
        },
    )
    start = perf_counter()
    try:
        before = bm25.read_corpus(factory, user_id, kb_id)
        outputs = {}
        for name in ("vector", "bm25"):
            entry = trace["routes"][name]
            route_start = perf_counter()
            try:
                if name == "vector":
                    items = retrieval.search(
                        factory,
                        user_id,
                        kb_id,
                        query,
                        CANDIDATE_LIMIT,
                        profile,
                        embedding_factory,
                    )
                else:
                    detail = {}
                    items = bm25.search(
                        factory, user_id, kb_id, query, CANDIDATE_LIMIT, trace=detail
                    )
                    entry["cost"] = detail["cost"]
                items = items[:CANDIDATE_LIMIT]
                outputs[name] = items
                entry.update(status="ok", items=[asdict(item) for item in items])
            except Exception as exc:
                transient = _transient_code(exc)
                entry.update(
                    status="transient_failure" if transient else "error",
                    error_code=transient or getattr(exc, "code", type(exc).__name__),
                )
                if not config.allow_degraded or transient is None:
                    raise
                trace["degraded"] = True
                outputs[name] = []
                # Authorization/integrity checks are never inside a degradation catch.
                current = bm25.read_corpus(factory, user_id, kb_id)
                if current != before:
                    raise RetrievalError(
                        "RRF_CORPUS_CHANGED", 409, "Retry on current corpus"
                    )
            finally:
                entry["elapsed_ms"] = (perf_counter() - route_start) * 1000
        current = bm25.read_corpus(factory, user_id, kb_id)
        if current != before:
            raise RetrievalError("RRF_CORPUS_CHANGED", 409, "Retry on current corpus")
        if all(r["status"] != "ok" for r in trace["routes"].values()):
            raise RetrievalError(
                "RRF_ALL_ROUTES_FAILED", 503, "Both retrieval routes failed"
            )
        sources = {row["chunk_id"]: row for row in current}
        for items in outputs.values():
            for item in items:
                row = sources.get(item.chunk_id)
                if row is None or any(
                    getattr(item, key) != row[key]
                    for key in (
                        "document_id",
                        "build_id",
                        "knowledge_base_id",
                        "document_name",
                        "text",
                        "page_number",
                        "heading_path",
                    )
                ):
                    raise RetrievalError(
                        "RRF_SOURCE_MISMATCH", 409, "Candidate source changed"
                    )
        fusion_start = perf_counter()
        items = fuse(outputs["vector"], outputs["bm25"], top_k)
        trace["fusion_ms"] = (perf_counter() - fusion_start) * 1000
        trace["status"] = "complete"
        return FusionResult(items, trace)
    except Exception as exc:
        trace.update(
            status="error", error_code=getattr(exc, "code", type(exc).__name__)
        )
        raise
    finally:
        trace["total_ms"] = (perf_counter() - start) * 1000
