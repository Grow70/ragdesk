"""Real lexical dev execution on an isolated fake-embedding index; no API calls."""

import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker
from test_retrieval import SECRET
from test_retrieval import retrieval_db as retrieval_db

from app import evaluate_bm25
from app.evaluation.data import ROOT
from app.llm.fake import FakeEmbeddingClient
from app.models import Document, KBMember
from app.services.auth import create_access_token
from app.services.ingest import EmbeddingProfile, ingest_document


def test_drafts_block_normal_evaluation_before_database(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("Draft gate must precede database settings")

    monkeypatch.setattr(evaluate_bm25, "load_settings", forbidden)
    output = tmp_path / "blocked"
    assert evaluate_bm25.main(["--run", "--output", str(output)]) == 2
    summary = json.loads((output / "summary.json").read_text())
    assert summary["n"] == summary["not_run_n"] == 15
    assert summary["retrieval"]["hit_at_5"] is None
    assert summary["blockers"] == ["UNREVIEWED_SAMPLES"]


def test_dev_draft_candidates_and_cost_on_isolated_db(
    retrieval_db, tmp_path, monkeypatch
):
    engine, ids = retrieval_db
    corpus_manifest = json.loads((ROOT / "data/sample_docs/manifest.json").read_text())
    storage = tmp_path / "uploads"
    (storage / "objects").mkdir(parents=True)
    with Session(engine) as session:
        session.add(KBMember(user_id=ids["alice"], kb_id=ids["b"], role="member"))
        session.commit()
    factory = sessionmaker(engine)
    for doc in corpus_manifest["documents"]:
        data = (ROOT / "data/sample_docs" / doc["path"]).read_bytes()
        key = f"objects/{uuid4().hex}"
        (storage / key).write_bytes(data)
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
    output = Path(os.environ.get("STEP17_BM25_OUTPUT", str(tmp_path / "dev")))
    assert (
        evaluate_bm25.main(
            [
                "--run",
                "--draft-diagnostics",
                "--mapping",
                str(mapping),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    rows = [
        json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()
    ]
    summary = json.loads((output / "summary.json").read_text())
    assert len(rows) == summary["completed_n"] == 15
    assert summary["evaluation_mode"] == "draft_diagnostics"
    assert summary["retrieval"]["n"] == 0 and summary["retrieval"]["hit_at_5"] is None
    assert summary["model_calls"] == 0
    for row in rows:
        assert row["cost"]["corpus_chunks"] > 0
        assert row["cost"]["tokenize_ms"] >= 0
        assert row["review_status"] == "draft"
        assert all(
            c["sample_document_id"].startswith(row["kb_id"] + "-") for c in row["items"]
        )
        assert all(
            c["distance"] is None and c["bm25_score"] is not None for c in row["items"]
        )

    # Exercise the formal branch with a fixture; do not edit source records.
    reviewed = json.loads((ROOT / "data/eval/questions.json").read_text())[:1]
    reviewed[0].update(
        review_status="reviewed",
        reviewed_by="unit-test-fixture",
        reviewed_at="2026-09-26T00:00:00+00:00",
    )
    question_path = tmp_path / "reviewed-fixture.json"
    question_path.write_text(json.dumps(reviewed))
    formal_output = tmp_path / "reviewed-fixture-output"
    assert (
        evaluate_bm25.main(
            [
                "--run",
                "--questions",
                str(question_path),
                "--mapping",
                str(mapping),
                "--output",
                str(formal_output),
            ]
        )
        == 0
    )
    formal = json.loads((formal_output / "summary.json").read_text())
    assert formal["evaluation_mode"] == "reviewed"
    assert formal["retrieval"]["n"] == 1
    assert formal["retrieval"]["hit_at_5"] in (0, 1)
