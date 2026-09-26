"""Optional RRF20 -> rerank -> top5 with fresh authorization on both sides."""

from dataclasses import asdict, replace
from time import perf_counter

from app.retrieval.reranker import CohereReranker, RerankError, validate_scores
from app.services import bm25, hybrid
from app.services.hybrid import FusionConfig, FusionResult
from app.services.retrieval import RetrievalError


def search(
    factory,
    user_id,
    kb_id,
    query,
    profile,
    embedding_factory,
    reranker_factory,
    *,
    enabled=False,
    config=FusionConfig(),
    trace=None,
) -> FusionResult:
    if type(enabled) is not bool:
        raise ValueError("enabled must be boolean")
    trace = trace if trace is not None else {}
    trace.update(
        status="running",
        enabled=enabled,
        degraded=False,
        rrf={},
        rrf_candidates=[],
        rerank={"status": "not_run", "call_count": 0, "search_units": None},
    )
    start = perf_counter()
    try:
        outcome = hybrid.search(
            factory,
            user_id,
            kb_id,
            query,
            profile,
            embedding_factory,
            top_k=20,
            config=config,
            trace=trace["rrf"],
        )
        trace["degraded"] = outcome.trace["degraded"]
        items = [replace(item, rrf_rank=item.rank) for item in outcome.items]
        trace["rrf_candidates"] = [asdict(item) for item in items]
        if not enabled or not items:
            trace["rerank"]["status"] = "disabled" if not enabled else "empty"
            trace["status"] = "complete"
            return FusionResult(items[:5], trace)
        before = bm25.read_corpus(factory, user_id, kb_id)
        sources = {row["chunk_id"]: row for row in before}
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
                    "RERANK_SOURCE_CHANGED", 409, "Retry current corpus"
                )
        detail = trace["rerank"]
        rerank_start = perf_counter()
        client = None
        result = None
        try:
            client = reranker_factory()
            detail["call_count"] = 1
            result = client.rerank(query, [item.text for item in items])
            ordered = validate_scores(result, len(items))
            detail.update(
                status="complete",
                search_units=result.search_units,
                raw_response=result.raw_response,
            )
        except RerankError as exc:
            detail.update(status="error", error_code=exc.code)
            if not exc.transient or exc.code not in {
                "RERANK_TIMEOUT",
                "RERANK_UNAVAILABLE",
            }:
                raise
            trace["degraded"] = True
            detail["status"] = "fallback"
        finally:
            if client is not None:
                client.close()
            detail["elapsed_ms"] = (perf_counter() - rerank_start) * 1000
        # Never catch this authorization/integrity failure in the fallback handler.
        if bm25.read_corpus(factory, user_id, kb_id) != before:
            raise RetrievalError("RERANK_SOURCE_CHANGED", 409, "Retry current corpus")
        if detail["status"] == "complete":
            items = [
                replace(items[score.index], rank=rank, rerank_score=score.score)
                for rank, score in enumerate(ordered, 1)
            ]
        trace["status"] = "complete"
        return FusionResult(items[:5], trace)
    except Exception as exc:
        trace.update(
            status="error", error_code=getattr(exc, "code", type(exc).__name__)
        )
        raise
    finally:
        trace["total_ms"] = (perf_counter() - start) * 1000


def search_configured(
    factory, user_id, kb_id, query, profile, embedding_factory, settings, *, trace=None
):
    """Application-facing entry: honor the environment switch, instantiate lazily."""

    def reranker_factory():
        if settings.cohere_api_key is None:
            raise RerankError("RERANK_INVALID_CONFIG")
        return CohereReranker(
            settings.cohere_api_key.get_secret_value(),
            model=settings.rerank_model,
            connect_timeout=settings.rerank_connect_timeout_seconds,
            read_timeout=settings.rerank_read_timeout_seconds,
        )

    return search(
        factory,
        user_id,
        kb_id,
        query,
        profile,
        embedding_factory,
        reranker_factory,
        enabled=settings.rerank_enabled,
        trace=trace,
    )
