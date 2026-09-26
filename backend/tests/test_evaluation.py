"""Hand-computed metric cases; these numbers are not model quality results."""

import json
from copy import deepcopy

import pytest

from app.evaluation.data import load_dataset
from app.evaluation.metrics import retrieval_metrics, summarize


def gold(quote="680 元", start=0):
    return {
        "document_id": "A-1",
        "section": "限额",
        "quote": quote,
        "locations": [
            {"section_index": 1, "char_start": start, "char_end": start + len(quote)}
        ],
    }


def candidate(text="680 元", doc="A-1", start=0, end=5):
    return {
        "sample_document_id": doc,
        "text": text,
        "source_spans": [{"section_index": 1, "char_start": start, "char_end": end}],
    }


def test_rank_recall_and_duplicate_units():
    evidence = [gold(), gold("15 天", 6), gold()]
    items = [candidate(doc="B-1"), candidate(), candidate()]
    result = retrieval_metrics(evidence, items)
    assert result == {
        "hit_at_5": 1,
        "evidence_recall_at_5": 0.5,
        "mrr_at_5": 0.5,
        "gold_units": 2,
        "matched_units": 1,
    }


def test_no_gold_no_hit_and_only_first_five():
    assert retrieval_metrics([], []) is None
    assert (
        retrieval_metrics([gold()], [candidate(doc="B-1")] * 5 + [candidate()])[
            "hit_at_5"
        ]
        == 0
    )
    assert retrieval_metrics([gold()], [])["mrr_at_5"] == 0
    # Same text at the wrong source position cannot count. Partial units do not count.
    assert retrieval_metrics([gold()], [candidate(start=9, end=20)])["hit_at_5"] == 0
    assert (
        retrieval_metrics(
            [gold()], [candidate("680", end=3), candidate(" 元", start=3)]
        )["hit_at_5"]
        == 0
    )


def test_denominators_errors_and_null_unrun_metrics():
    rows = [
        {
            "answerable": True,
            "execution": "complete",
            "answer_status": "answered",
            "retrieval_metrics": retrieval_metrics([gold()], [candidate()]),
            "citation_valid": True,
            "retrieval_ms": 2,
            "total_ms": 4,
            "calls": [],
        },
        {
            "answerable": False,
            "execution": "complete",
            "answer_status": "insufficient_evidence",
            "retrieval_metrics": None,
            "citation_valid": None,
            "retrieval_ms": 1,
            "total_ms": 1,
            "calls": [],
        },
        {
            "answerable": False,
            "execution": "error",
            "answer_status": None,
            "retrieval_metrics": None,
            "citation_valid": None,
            "retrieval_ms": None,
            "total_ms": 3,
            "calls": [],
        },
    ]
    report = summarize(rows)
    assert report["retrieval"]["n"] == 1 and report["retrieval"]["hit_at_5"] == 1
    assert report["unanswerable"]["n"] == 2
    assert report["unanswerable"]["completed_n"] == 1
    assert report["unanswerable"]["insufficient_evidence"] == 1
    assert report["unanswerable"]["errors"] == 1
    assert summarize([])["retrieval"]["hit_at_5"] is None
    assert summarize([])["timing"]["total_ms_mean"] is None


def test_dataset_real_drafts_are_blocked_and_gold_positions_resolve():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    data = load_dataset(
        root / "data/eval/questions.json",
        root / "data/sample_docs/manifest.json",
        "dev",
    )
    assert len(data["questions"]) == 15
    assert data["blockers"] == ["UNREVIEWED_SAMPLES"]
    assert all(
        g["locations"] for row in data["questions"] for g in row["gold_evidence"]
    )


def test_duplicate_ids_and_forged_source_are_rejected(tmp_path):
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    records = json.loads((root / "data/eval/questions.json").read_text())
    path = tmp_path / "questions.json"
    records.append(deepcopy(records[0]))
    path.write_text(json.dumps(records))
    with pytest.raises(ValueError, match="DUPLICATE_ID"):
        load_dataset(path, root / "data/sample_docs/manifest.json", "dev")
    records.pop()
    records[0]["gold_evidence"][0]["quote"] = "虚构不存在的金额"
    path.write_text(json.dumps(records))
    with pytest.raises(ValueError, match="GOLD_NOT_FOUND"):
        load_dataset(path, root / "data/sample_docs/manifest.json", "dev")


def test_cli_saves_unrun_baseline_without_loading_model_config(tmp_path, monkeypatch):
    import json

    from app import evaluate

    def forbidden():
        raise AssertionError(
            "Draft samples must not reach model/database configuration"
        )

    monkeypatch.setattr(evaluate, "load_settings", forbidden)
    output = tmp_path / "baseline"
    assert evaluate.main(["--run-real", "--output", str(output)]) == 2
    manifest = json.loads((output / "manifest.json").read_text())
    summary = json.loads((output / "summary.json").read_text())
    rows = [
        json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()
    ]
    assert manifest["split"] == "dev"
    assert manifest["blockers"] == ["UNREVIEWED_SAMPLES"]
    assert len(rows) == 15 and all(row["execution"] == "not_run" for row in rows)
    assert (
        summary["retrieval"]["hit_at_5"] is None
        and summary["tokens"]["total_tokens"] is None
    )
    assert manifest["code"]["source_tree_sha256"] and manifest["dataset_sha256"]


def test_unknown_token_usage_is_not_zero():
    row = {
        "answerable": True,
        "execution": "error",
        "retrieval_metrics": None,
        "calls": [{"usage": None, "usage_complete": False}],
    }
    tokens = summarize([row])["tokens"]
    assert tokens["total_tokens"] is None and tokens["unknown_usage_calls"] == 1


@pytest.mark.parametrize(
    "status, body, code",
    [
        (200, "not JSON", "MODEL_INVALID_RESPONSE"),
        (401, "invalid key: test-secret-for-response", "MODEL_AUTH_ERROR"),
    ],
)
def test_preserve_invalid_provider_output_without_request_secrets(status, body, code):
    import httpx

    from app.evaluation.runtime import ObservedClient, RecordedClient
    from app.llm.contracts import ModelError
    from app.llm.openai import OpenAIEmbeddingClient

    recorded = RecordedClient(
        OpenAIEmbeddingClient,
        api_key="test-secret-for-response",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text=body)
        ),
    )
    calls = []
    client = ObservedClient(recorded, calls, "embedding")
    try:
        with pytest.raises(ModelError) as error:
            client.embed_query("问题")
        assert error.value.code == code
        assert calls[0]["attempts"] == 1
        assert calls[0]["usage"] is None
        raw = calls[0]["raw_http_responses"][0]
        assert raw["status_code"] == status
        assert "test-secret-for-response" not in json.dumps(calls)
        assert raw["body"] == body.replace(
            "test-secret-for-response", "[REDACTED_API_KEY]"
        )
    finally:
        client.close()
    assert recorded.http.is_closed


def test_review_metadata_and_test_freeze_gate(tmp_path):
    from app.evaluation.data import ROOT, canonical, digest

    records = json.loads((ROOT / "data/eval/questions.json").read_text())
    manifest = ROOT / "data/sample_docs/manifest.json"
    path = tmp_path / "questions.json"
    for row in records:
        row.update(
            review_status="reviewed",
            reviewed_by="unit-test-reviewer",
            reviewed_at="2026-09-26T10:00:00+08:00",
        )
    path.write_text(json.dumps(records))
    assert load_dataset(path, manifest)["blockers"] == []
    with pytest.raises(ValueError, match="TEST_FREEZE_MISMATCH"):
        load_dataset(path, manifest, "test")
    frozen = [row for row in records if row["split"] == "test"]
    (tmp_path / "test.freeze.sha256").write_text(digest(canonical(frozen)))
    assert load_dataset(path, manifest, "test")["blockers"] == []
    records[0].pop("reviewed_by")
    path.write_text(json.dumps(records))
    with pytest.raises(ValueError, match="MISSING_REVIEWER"):
        load_dataset(path, manifest)


def test_citation_legality_does_not_use_filename_or_score_refusal_as_valid():
    from app.evaluation.runtime import citation_validity

    assert citation_validity({}, None) is None
    for ids in (["forged"], [], ["c1", "c1"], [{}]):
        trace = {
            "raw_chat": {"content": {"status": "answered", "citation_ids": ids}},
            "evidence": {"c1": {}},
        }
        assert citation_validity(trace, None) is False
    assert (
        citation_validity(
            {
                "raw_chat": {
                    "content": {"status": "insufficient_evidence", "citation_ids": []}
                }
            },
            None,
        )
        is None
    )
