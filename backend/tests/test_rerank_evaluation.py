"""Dev pipeline diagnostics remain explicitly fake and separate from quality."""

import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker
from test_retrieval import SECRET
from test_retrieval import retrieval_db as retrieval_db

from app import evaluate_rerank
from app.evaluation.data import ROOT
from app.llm.fake import FakeEmbeddingClient
from app.models import Document, KBMember
from app.services.auth import create_access_token
from app.services.ingest import EmbeddingProfile, ingest_document


def test_real_run_blocks_drafts_and_missing_key_without_fallback(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("Preflight must block before database/model setup")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    monkeypatch.setattr(evaluate_rerank, "load_settings", forbidden)
    output = tmp_path / "blocked"
    assert evaluate_rerank.main(["--run-real", "--output", str(output)]) == 2
    summary = json.loads((output / "summary.json").read_text())
    assert summary["n"] == summary["not_run_n"] == 15
    assert summary["blockers"] == [
        "UNREVIEWED_SAMPLES",
        "REAL_MODEL_KEY_REQUIRED",
        "RERANK_API_KEY_REQUIRED",
    ]
    assert all(
        summary["methods"][method]["hit_at_5"] is None
        for method in ("rrf", "rrf_rerank")
    )
    assert summary["paired_n"] == 0


def test_dev_fake_comparison_same_candidates_once(retrieval_db, tmp_path, monkeypatch):
    engine, ids = retrieval_db
    docs = json.loads((ROOT / "data/sample_docs/manifest.json").read_text())[
        "documents"
    ]
    storage = tmp_path / "uploads"
    (storage / "objects").mkdir(parents=True)
    with Session(engine) as session:
        session.add(KBMember(user_id=ids["alice"], kb_id=ids["b"], role="member"))
        session.commit()
    factory = sessionmaker(engine)
    for doc in docs:
        key = f"objects/{uuid4().hex}"
        (storage / key).write_bytes(
            (ROOT / "data/sample_docs" / doc["path"]).read_bytes()
        )
        document_id = uuid4()
        with Session(engine) as session:
            session.add(
                Document(
                    id=document_id,
                    kb_id=ids[doc["kb_key"].lower()],
                    file_name=Path(doc["path"]).name,
                    file_sha256=doc["file_sha256"],
                    storage_key=key,
                )
            )
            session.commit()
        outcome = ingest_document(
            factory,
            document_id,
            storage,
            FakeEmbeddingClient(),
            EmbeddingProfile.fake(),
        )
        assert outcome.status == "ready"
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"A": str(ids["a"]), "B": str(ids["b"])}))
    monkeypatch.setenv(
        "EVAL_BEARER_TOKEN", create_access_token(ids["alice"], SECRET, 30)
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    output = Path(os.environ.get("STEP19_RERANK_OUTPUT", str(tmp_path / "comparison")))
    assert (
        evaluate_rerank.main(
            ["--fake-diagnostics", "--mapping", str(mapping), "--output", str(output)]
        )
        == 0
    )
    rows = [
        json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()
    ]
    summary = json.loads((output / "summary.json").read_text())
    assert len(rows) == summary["completed_n"] == 15
    assert summary["mode"] == "fake_diagnostics" and summary["paired_n"] == 0
    assert summary["observed_model_calls"] == 15
    assert summary["rerank_minus_rrf"]["hit_at_5"] is None
    for row in rows:
        assert len(row["calls"]) == 1 and row["calls"][0]["attempts"] == 1
        assert row["comparison_eligible"] is False
        assert len(row["items"]["rrf"]) <= 5
        assert len(row["trace"]["rrf_candidates"]) <= 20
        assert row["items"]["rrf"] == [
            {
                **c,
                **{
                    k: row["items"]["rrf"][i][k]
                    for k in ("sample_document_id", "source_spans")
                },
            }
            for i, c in enumerate(row["trace"]["rrf_candidates"][:5])
        ]
        expected_ids = [
            c["chunk_id"] for c in reversed(row["trace"]["rrf_candidates"])
        ][:5]
        assert [c["chunk_id"] for c in row["items"]["rrf_rerank"]] == expected_ids
        assert row["trace"]["rerank"]["call_count"] == 1
        assert row["trace"]["rerank"]["raw_response"] == {"fake": True}
        for candidate in row["items"]["rrf_rerank"]:
            assert candidate["sample_document_id"].startswith(row["kb_id"] + "-")
    assert summary["rerank_calls"] == summary["rerank_unknown_usage_calls"] == 15
    assert summary["reported_search_units"] is None
    assert summary["default_enabled"] is False


def test_summary_signed_deltas_excludes_fallback_and_no_gold():
    from app.evaluation.rerank import summarize

    complete = {
        "execution": "complete",
        "comparison_eligible": True,
        "metrics": {
            "rrf": {"hit_at_5": 1, "evidence_recall_at_5": 0.5, "mrr_at_5": 1},
            "rrf_rerank": {"hit_at_5": 1, "evidence_recall_at_5": 1, "mrr_at_5": 0.5},
        },
        "trace": {
            "rrf": {"total_ms": 2},
            "total_ms": 6,
            "rerank": {"elapsed_ms": 3, "call_count": 1, "search_units": 1},
        },
    }
    no_gold = {
        **complete,
        "comparison_eligible": False,
        "metrics": {"rrf": None, "rrf_rerank": None},
    }
    fallback = {
        **no_gold,
        "trace": {"degraded": True, "rerank": {"call_count": 1, "search_units": None}},
    }
    summary = summarize([complete, no_gold, fallback])
    assert summary["n"] == 3 and summary["paired_n"] == 1
    assert summary["rerank_minus_rrf"] == {
        "hit_at_5": 0,
        "evidence_recall_at_5": 0.5,
        "mrr_at_5": -0.5,
    }
    assert summary["reported_search_units"] == 2
    assert summary["rerank_unknown_usage_calls"] == 1
    assert summary["currency_cost"] is None
