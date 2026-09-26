"""Deterministic strict evidence metrics; no model-based correctness judging."""

import re
from statistics import mean


def _text(value):
    return re.sub(r"\s+", "", value)


def _matches(gold, item):
    if item.get("sample_document_id") != gold["document_id"]:
        return False
    if _text(gold["quote"]) not in _text(item["text"]):
        return False
    for location in gold["locations"]:
        intervals = sorted(
            (span["char_start"], span["char_end"])
            for span in item.get("source_spans", [])
            if span["section_index"] == location["section_index"]
        )
        covered = location["char_start"]
        for low, high in intervals:
            if low <= covered:
                covered = max(covered, high)
        if covered >= location["char_end"]:
            return True
    return False


def retrieval_metrics(gold, items):
    units = list(
        {(g["document_id"], g["section"], g["quote"]): g for g in gold}.values()
    )
    if not units:
        return None
    matched = set()
    first = None
    for rank, item in enumerate(items[:5], 1):
        hits = {index for index, unit in enumerate(units) if _matches(unit, item)}
        matched.update(hits)
        if hits and first is None:
            first = rank
    return {
        "hit_at_5": int(bool(matched)),
        "evidence_recall_at_5": len(matched) / len(units),
        "mrr_at_5": 1 / first if first else 0,
        "gold_units": len(units),
        "matched_units": len(matched),
    }


def summarize(rows):
    measured = [
        r["retrieval_metrics"] for r in rows if r.get("retrieval_metrics") is not None
    ]
    retrieval = {"n": len(measured)}
    for key in ("hit_at_5", "evidence_recall_at_5", "mrr_at_5"):
        retrieval[key] = mean(m[key] for m in measured) if measured else None
    retrieval["gold_units"] = sum(m["gold_units"] for m in measured)
    retrieval["matched_units"] = sum(m["matched_units"] for m in measured)
    result = {"n": len(rows), "retrieval": retrieval}
    for label, answerable in [("answerable", True), ("unanswerable", False)]:
        group = [r for r in rows if r["answerable"] is answerable]
        completed = [r for r in group if r["execution"] == "complete"]
        counts = {
            status: sum(r.get("answer_status") == status for r in completed)
            for status in ("answered", "insufficient_evidence", "needs_clarification")
        }
        result[label] = {
            "n": len(group),
            "completed_n": len(completed),
            **counts,
            "errors": sum(r["execution"] == "error" for r in group),
            "not_run": sum(r["execution"] == "not_run" for r in group),
            "refusal_rate": counts["insufficient_evidence"] / len(completed)
            if completed
            else None,
        }
    checked = [r["citation_valid"] for r in rows if r.get("citation_valid") is not None]
    result["citations"] = {
        "n": len(checked),
        "valid_n": sum(checked),
        "valid_rate": sum(checked) / len(checked) if checked else None,
    }
    timing = {}
    for field in ("retrieval_ms", "total_ms"):
        values = [r[field] for r in rows if r.get(field) is not None]
        timing[field + "_n"] = len(values)
        timing[field + "_mean"] = mean(values) if values else None
    result["timing"] = timing
    calls = [call for row in rows for call in row.get("calls", [])]
    executed = any(row["execution"] != "not_run" for row in rows)
    unknown = sum(not call["usage_complete"] for call in calls)
    result["tokens"] = {
        "known_reported_total": sum(
            c["usage"]["total_tokens"] for c in calls if c.get("usage")
        )
        if executed
        else None,
        "unknown_usage_calls": unknown,
        "total_tokens": sum(c["usage"]["total_tokens"] for c in calls)
        if executed and not unknown
        else None,
        "note": "Reported tokens only; failure/retry usage may be unknown.",
    }
    result["manual_review"] = (
        "pending: factual correctness and citation support are not automatically scored"
    )
    return result
