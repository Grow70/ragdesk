"""Paired outcomes: source legality is never a substitute for human judgement."""

from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from math import ceil
from statistics import mean, median

from app.evaluation.data import canonical, digest
from app.services.traces import estimate

ARMS = ("fixed", "agent")


def fingerprint(row, arm):
    return digest(canonical({"question": row["sample"], "output": row[arm]}))


def worksheet(rows):
    return [
        {
            "id": row["sample"]["id"],
            "arm": arm,
            "output_sha256": fingerprint(row, arm),
            "factual_correct": None,
            "evidence_supported": None,
            "task_success": None,
            "reviewer": "",
            "reviewed_at": "",
            "notes": "",
        }
        for row in rows
        for arm in ARMS
        if row[arm]["execution"] == "complete"
    ]


def reviews_by_key(rows, reviews):
    expected = {(r["id"], r["arm"]): r for r in worksheet(rows)}
    checked = {}
    originals = {(r["sample"]["id"], a): r for r in rows for a in ARMS}
    for review in reviews:
        key = (review.get("id"), review.get("arm"))
        if key in checked or key not in expected:
            raise ValueError("INVALID_REVIEW_ID")
        if review.get("output_sha256") != expected[key]["output_sha256"]:
            raise ValueError("REVIEW_OUTPUT_MISMATCH")
        labels = [
            review.get(k)
            for k in ("factual_correct", "evidence_supported", "task_success")
        ]
        if labels == [None, None, None]:
            checked[key] = None
            continue
        if (
            any(type(value) is not bool for value in labels)
            or not isinstance(review.get("reviewer"), str)
            or not review["reviewer"].strip()
            or not isinstance(review.get("notes"), str)
            or not review["notes"].strip()
        ):
            raise ValueError("INCOMPLETE_HUMAN_REVIEW")
        try:
            datetime.fromisoformat(review["reviewed_at"])
        except (ValueError, TypeError, KeyError):
            raise ValueError("INVALID_REVIEW_DATE") from None
        original = originals[key]
        status = original[key[1]]["result"]["status"]
        if labels[2] and (
            (status == "answered" and not all(labels[:2]))
            or (status == "insufficient_evidence" and original["sample"]["answerable"])
        ):
            raise ValueError("INCONSISTENT_SUCCESS_LABEL")
        checked[key] = {"supported_answer": all(labels[:2]), "task_success": labels[2]}
    return checked


def distribution(values):
    known = sorted(v for v in values if v is not None)
    return {
        "n": len(known),
        "unknown_n": len(values) - len(known),
        "mean": mean(known) if known else None,
        "p50": median(known) if known else None,
        "p95": known[ceil(0.95 * len(known)) - 1] if known else None,
    }


def resources(trace, prices):
    """All attempts are required for a complete total; preserve partial usage."""
    calls = [
        call for slot in trace.get("models", {}).values() for call in slot["calls"]
    ]
    day = date.fromisoformat(trace["created_at"][:10]) if calls else date.today()
    costs = [
        estimate(
            prices,
            c["provider"],
            c["model"],
            c["usage"],
            c["usage_status"] == "reported",
            day,
        )
        for c in calls
    ]
    known = [c for c in costs if c["amount"] is not None]
    currencies = {c["currency"] for c in known}
    subtotal = (
        str(sum((Decimal(c["amount"]) for c in known), Decimal(0)))
        if len(currencies) == 1
        else None
    )
    complete = bool(calls) and not trace.get("trace_incomplete", False)
    usage_complete = complete and all(c["usage_status"] == "reported" for c in calls)
    usage = (
        {
            key: sum(c["usage"][key] for c in calls)
            for key in ("input_tokens", "output_tokens", "total_tokens")
        }
        if usage_complete
        else None
    )
    return {
        "usage": usage,
        "calls": calls,
        "costs": costs,
        "estimated_total": subtotal if complete and len(known) == len(costs) else None,
        "known_subtotal": subtotal,
        "currency": next(iter(currencies)) if len(currencies) == 1 else None,
    }


def summarize(rows, *, mode, reviews=(), _categories=True):
    checked = reviews_by_key(rows, reviews)
    report = {
        "mode": mode,
        "selected_n": len(rows),
        "categories": dict(Counter(r["sample"]["category"] for r in rows)),
        "ambiguous_coverage": "not_labelled; paraphrase is not ambiguity",
        "quality_status": "independent_human_review_required",
        "latency_percentiles": "P50 median; P95 nearest rank ceil(0.95*n)",
        "arms": {},
    }
    comparable = [
        r for r in rows if all(r[a]["execution"] in {"complete", "error"} for a in ARMS)
    ]
    report["comparable_pairs_n"] = len(comparable)
    paired = {"agent_wins": 0, "fixed_wins": 0, "ties": 0, "n": 0}
    for arm in ARMS:
        eligible = comparable
        completed = [r for r in eligible if r[arm]["execution"] == "complete"]
        answerable = [r for r in completed if r["sample"]["answerable"]]
        unanswerable = [r for r in completed if not r["sample"]["answerable"]]
        answered = [r for r in completed if r[arm]["result"]["status"] == "answered"]
        judgements = [
            (checked.get((r["sample"]["id"], arm)) or {}).get("supported_answer")
            for r in answered
        ]
        reviewed = [v for v in judgements if v is not None]
        real = mode == "real"
        resources_list = [r[arm].get("resources", {}) for r in eligible]
        usages = [v.get("usage") for v in resources_list]
        costs = [v.get("estimated_total") for v in resources_list]
        currencies = {v.get("currency") for v in resources_list}
        success, failure = [], []
        if real:
            for row in eligible:
                key = row["sample"]["id"]
                verdict = (checked.get((key, arm)) or {}).get("task_success")
                if row[arm]["execution"] == "error" or verdict is False:
                    failure.append(key)
                elif verdict is True:
                    success.append(key)
        report["arms"][arm] = {
            "attempted_n": len(eligible),
            "observed_attempts_n": sum(r[arm]["execution"] != "not_run" for r in rows),
            "excluded_unpaired_or_invalidated_n": sum(
                r[arm]["execution"] != "not_run" for r in rows
            )
            - len(eligible),
            "completed_n": len(completed),
            "errors_n": len(eligible) - len(completed),
            "answered_n": len(answered),
            "reviewed_answers_n": len(reviewed) if real else 0,
            "correct_and_supported": {
                "passed": sum(reviewed) if real else None,
                "reviewed_denominator": len(reviewed) if real else 0,
                "ratio_on_reviewed_answers": mean(reviewed)
                if real and reviewed
                else None,
                "ratio_on_all_answers": mean(reviewed)
                if real and reviewed and len(reviewed) == len(answered)
                else None,
            },
            "unanswerable": {
                "completed_n": len(unanswerable),
                "refused_n": sum(
                    r[arm]["result"]["status"] == "insufficient_evidence"
                    for r in unanswerable
                ),
                "clarification_n": sum(
                    r[arm]["result"]["status"] == "needs_clarification"
                    for r in unanswerable
                ),
            },
            "answerable": {
                "completed_n": len(answerable),
                "wrong_refusal_n": sum(
                    r[arm]["result"]["status"] == "insufficient_evidence"
                    for r in answerable
                ),
                "clarification_n": sum(
                    r[arm]["result"]["status"] == "needs_clarification"
                    for r in answerable
                ),
            },
            "tool_calls": distribution(
                [r[arm].get("tool_call_count") for r in eligible]
            ),
            "model_requests": distribution(
                [r[arm].get("model_call_count") for r in eligible]
            ),
            "latency_ms_all_attempts": distribution(
                [r[arm].get("total_ms") for r in eligible]
            ),
            "latency_ms_completed": distribution(
                [r[arm].get("total_ms") for r in completed]
            ),
            "usage_known_n": sum(v is not None for v in usages),
            "usage_total": {k: sum(v[k] for v in usages) for k in usages[0]}
            if usages and all(v is not None for v in usages)
            else None,
            "estimated_cost_total": str(sum(Decimal(v) for v in costs))
            if costs and all(v is not None for v in costs) and len(currencies) == 1
            else None,
            "cost_currency": next(iter(currencies)) if len(currencies) == 1 else None,
            "success_trajectory_ids": success[:3],
            "success_available_n": len(success),
            "failure_trajectory_ids": failure[:3],
            "failure_available_n": len(failure),
        }
    if mode == "real":
        for row in comparable:
            a, b = (
                (checked.get((row["sample"]["id"], arm)) or {}).get("task_success")
                for arm in ARMS
            )
            if a is not None and b is not None:
                paired["n"] += 1
                paired["ties" if a == b else "agent_wins" if b else "fixed_wins"] += 1
    for arm in ARMS:
        metrics = report["arms"][arm]
        for group, count_key, ratio_key in (
            ("unanswerable", "refused_n", "refusal_ratio_on_completed"),
            ("answerable", "wrong_refusal_n", "wrong_refusal_ratio_on_completed"),
        ):
            item = metrics[group]
            item[ratio_key] = (
                item[count_key] / item["completed_n"] if item["completed_n"] else None
            )
        usage = metrics["usage_total"]
        metrics["usage_mean_per_attempt"] = (
            {key: value / metrics["attempted_n"] for key, value in usage.items()}
            if usage is not None and metrics["attempted_n"]
            else None
        )
    report["paired_human_verdicts"] = paired
    if _categories:
        report["by_category"] = {
            category: summarize(
                [r for r in rows if r["sample"]["category"] == category],
                mode=mode,
                reviews=[
                    v
                    for v in reviews
                    if v["id"]
                    in {
                        r["sample"]["id"]
                        for r in rows
                        if r["sample"]["category"] == category
                    }
                ],
                _categories=False,
            )
            for category in report["categories"]
        }
    return report
