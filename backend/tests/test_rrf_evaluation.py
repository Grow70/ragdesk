"""Dev pipeline diagnostics remain explicitly fake and separate from quality."""

import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker
from test_retrieval import SECRET
from test_retrieval import retrieval_db as retrieval_db

from app import evaluate_rrf
from app.evaluation.data import ROOT
from app.llm.fake import FakeEmbeddingClient
from app.models import Document, KBMember
from app.services.auth import create_access_token
from app.services.ingest import EmbeddingProfile, ingest_document


def test_real_run_blocks_drafts_and_missing_key_without_fallback(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("Preflight must block before database/model setup")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(evaluate_rrf, "load_settings", forbidden)
    output = tmp_path / "blocked"
    assert evaluate_rrf.main(["--run-real", "--output", str(output)]) == 2
    summary = json.loads((output / "summary.json").read_text())
    assert summary["n"] == summary["not_run_n"] == 15
    assert summary["blockers"] == ["UNREVIEWED_SAMPLES", "REAL_MODEL_KEY_REQUIRED"]
    assert all(
        summary["methods"][method]["hit_at_5"] is None
        for method in ("vector", "bm25", "rrf")
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
    output = Path(os.environ.get("STEP18_RRF_OUTPUT", str(tmp_path / "comparison")))
    assert (
        evaluate_rrf.main(
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
    assert summary["rrf_minus"]["vector"]["hit_at_5"] is None
    for row in rows:
        assert len(row["calls"]) == 1 and row["calls"][0]["attempts"] == 1
        assert row["comparison_eligible"] is False
        assert len(row["items"]["rrf"]) <= 5
        assert all(
            len(route["items"]) <= 20 for route in row["trace"]["routes"].values()
        )
        for method in ("vector", "bm25"):
            assert [c["chunk_id"] for c in row["items"][method]] == [
                c["chunk_id"] for c in row["trace"]["routes"][method]["items"][:5]
            ]
        for candidate in row["items"]["rrf"]:
            assert candidate["sample_document_id"].startswith(row["kb_id"] + "-")
            expected = sum(
                1 / (60 + candidate[key])
                for key in ("vector_rank", "bm25_rank")
                if candidate[key] is not None
            )
            assert abs(candidate["rrf_score"] - expected) < 1e-12
