"""Paired RRF/RRF+rerank diagnostics and reviewed real evaluation."""

from dataclasses import asdict
from statistics import mean

from app.evaluation.metrics import retrieval_metrics
from app.evaluation.runtime import ObservedClient
from app.services.hybrid import FusionConfig
from app.services.reranked import search

METHODS = ("rrf", "rrf_rerank")
METRICS = ("hit_at_5", "evidence_recall_at_5", "mrr_at_5")


def run_question(
    question,
    factory,
    user_id,
    kb_id,
    profile,
    embedding_factory,
    index,
    index_check,
    *,
    mode,
    reranker_factory,
    allow_degraded=False,
):
    row = {
        **question,
        "execution": "error",
        "mode": mode,
        "calls": [],
        "comparison_eligible": False,
        "items": {name: [] for name in METHODS},
        "metrics": {name: None for name in METHODS},
    }
    trace = {}
    try:
        if index_check() != index["sha256"]:
            raise ValueError("INDEX_CHANGED")
        result = search(
            factory,
            user_id,
            kb_id,
            question["question"],
            profile,
            lambda: ObservedClient(embedding_factory(), row["calls"], "embedding"),
            reranker_factory,
            enabled=True,
            config=FusionConfig(allow_degraded),
            trace=trace,
        )
        if index_check() != index["sha256"]:
            raise ValueError("INDEX_CHANGED")
        candidates = {
            "rrf": trace["rrf_candidates"][:5],
            "rrf_rerank": [asdict(item) for item in result.items],
        }
        for method, items in candidates.items():
            row["items"][method] = [
                {**item, **index["chunk_map"][str(item["chunk_id"])]} for item in items
            ]
        row["execution"] = "complete"
        if (
            mode == "real"
            and not trace["degraded"]
            and trace["rerank"]["status"] == "complete"
        ):
            for method, items in row["items"].items():
                row["metrics"][method] = retrieval_metrics(
                    question["gold_evidence"], items
                )
            row["comparison_eligible"] = all(
                m is not None for m in row["metrics"].values()
            )
    except Exception as exc:
        row["error_code"] = getattr(
            exc,
            "code",
            "INDEX_CHANGED" if str(exc) == "INDEX_CHANGED" else type(exc).__name__,
        )
        row["metrics"] = {name: None for name in METHODS}
        row["comparison_eligible"] = False
        row["execution"] = "error"
    row["trace"] = trace
    return row


def summarize(rows):
    paired = [r for r in rows if r["comparison_eligible"]]
    methods = {
        method: {
            key: mean(r["metrics"][method][key] for r in paired) if paired else None
            for key in METRICS
        }
        for method in METHODS
    }
    timings = {}
    for name, path in (
        ("rrf", ("rrf", "total_ms")),
        ("rerank_increment", ("rerank", "elapsed_ms")),
        ("rrf_rerank", ("total_ms",)),
    ):
        values = []
        for row in rows:
            value = row.get("trace", {})
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            if value is not None:
                values.append(value)
        timings[name] = {"n": len(values), "mean_ms": mean(values) if values else None}
    calls = [c for row in rows for c in row.get("calls", [])]
    reranks = [r.get("trace", {}).get("rerank", {}) for r in rows]
    units = [r["search_units"] for r in reranks if r.get("search_units") is not None]
    return {
        "n": len(rows),
        "paired_n": len(paired),
        "excluded_n": len(rows) - len(paired),
        "completed_n": sum(r["execution"] == "complete" for r in rows),
        "error_n": sum(r["execution"] == "error" for r in rows),
        "not_run_n": sum(r["execution"] == "not_run" for r in rows),
        "degraded_n": sum(r.get("trace", {}).get("degraded", False) for r in rows),
        "methods": methods,
        "rerank_minus_rrf": {
            key: methods["rrf_rerank"][key] - methods["rrf"][key] if paired else None
            for key in METRICS
        },
        "timings": timings,
        "observed_model_calls": len(calls),
        "reported_embedding_tokens": sum(
            c["usage"]["total_tokens"] for c in calls if c.get("usage")
        )
        if calls
        else None,
        "embedding_unknown_usage_calls": sum(not c["usage_complete"] for c in calls),
        "rerank_calls": sum(r.get("call_count", 0) for r in reranks),
        "reported_search_units": sum(units) if units else None,
        "rerank_unknown_usage_calls": sum(
            r.get("call_count", 0) for r in reranks if r.get("search_units") is None
        ),
        "currency_cost": None,
        "default_enabled": False,
        "conclusion": "Assess signed deltas, latency and billed units before enabling."
        if paired
        else "Real comparison unverified; disabled by default.",
    }
