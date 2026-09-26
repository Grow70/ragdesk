"""Paired retrieval comparison using the same two candidate lists per question."""

from dataclasses import asdict
from statistics import mean

from app.evaluation.metrics import retrieval_metrics
from app.evaluation.runtime import ObservedClient
from app.services.hybrid import FusionConfig, search

METHODS = ("vector", "bm25", "rrf")
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
        outcome = search(
            factory,
            user_id,
            kb_id,
            question["question"],
            profile,
            lambda: ObservedClient(embedding_factory(), row["calls"], "embedding"),
            config=FusionConfig(allow_degraded),
            trace=trace,
        )
        if index_check() != index["sha256"]:
            raise ValueError("INDEX_CHANGED")
        candidates = {
            "vector": trace["routes"]["vector"]["items"][:5],
            "bm25": trace["routes"]["bm25"]["items"][:5],
            "rrf": [asdict(item) for item in outcome.items],
        }
        for method, items in candidates.items():
            row["items"][method] = [
                {**item, **index["chunk_map"][str(item["chunk_id"])]} for item in items
            ]
        row["execution"] = "complete"
        if mode == "real" and not trace["degraded"]:
            for method, items in row["items"].items():
                row["metrics"][method] = retrieval_metrics(
                    question["gold_evidence"], items
                )
            row["comparison_eligible"] = all(
                m is not None for m in row["metrics"].values()
            )
    except Exception as exc:
        row.update(
            execution="error",
            comparison_eligible=False,
            metrics={name: None for name in METHODS},
        )
        row["error_code"] = getattr(
            exc,
            "code",
            "INDEX_CHANGED" if str(exc) == "INDEX_CHANGED" else type(exc).__name__,
        )
    row["trace"] = trace
    return row


def summarize(rows):
    paired = [row for row in rows if row["comparison_eligible"]]
    methods = {
        method: {
            key: mean(r["metrics"][method][key] for r in paired) if paired else None
            for key in METRICS
        }
        for method in METHODS
    }
    delta = {
        method: {
            key: methods["rrf"][key] - methods[method][key] if paired else None
            for key in METRICS
        }
        for method in ("vector", "bm25")
    }
    timings = {}
    for method in METHODS:
        values = [
            r["trace"]["routes"][method]["elapsed_ms"]
            for r in rows
            if method != "rrf"
            and "elapsed_ms" in r.get("trace", {}).get("routes", {}).get(method, {})
        ]
        if method == "rrf":
            values = [
                r["trace"]["total_ms"] for r in rows if "total_ms" in r.get("trace", {})
            ]
        timings[method] = {
            "n": len(values),
            "mean_ms": mean(values) if values else None,
        }
    calls = [c for row in rows for c in row.get("calls", [])]
    return {
        "n": len(rows),
        "paired_n": len(paired),
        "excluded_n": len(rows) - len(paired),
        "completed_n": sum(r["execution"] == "complete" for r in rows),
        "error_n": sum(r["execution"] == "error" for r in rows),
        "not_run_n": sum(r["execution"] == "not_run" for r in rows),
        "degraded_n": sum(r.get("trace", {}).get("degraded", False) for r in rows),
        "methods": methods,
        "rrf_minus": delta,
        "timings": timings,
        "observed_model_calls": len(calls),
        "reported_tokens": sum(
            c["usage"]["total_tokens"] for c in calls if c.get("usage")
        )
        if calls
        else None,
        "unknown_usage_calls": sum(not c["usage_complete"] for c in calls),
        "conclusion": "Signed deltas only; no automatic overall quality claim."
        if paired
        else "No verified paired comparison; improvement is unknown.",
    }
