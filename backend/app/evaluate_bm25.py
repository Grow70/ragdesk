"""Independent BM25 dev baseline; no Chat, Embedding, or vector baseline writes."""

import argparse
import json
import os
import platform
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from statistics import mean
from uuid import UUID, uuid4

import jieba
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import load_settings
from app.evaluate import code_version, write_json
from app.evaluation.data import ROOT, canonical, digest, load_dataset
from app.evaluation.metrics import retrieval_metrics
from app.evaluation.runtime import authenticate
from app.retrieval.bm25 import PARAMETERS, PREPROCESSING_VERSION
from app.services.bm25 import read_corpus, search


def snapshot(factory, actor, mapping, data):
    corpora = {}
    for key in sorted({q["kb_id"] for q in data["questions"]}):
        corpus = read_corpus(factory, actor, UUID(mapping[key]))
        docs = {d["file_sha256"]: d for d in data["documents"] if d["kb_key"] == key}
        if {row["file_sha256"] for row in corpus} != docs.keys():
            raise ValueError("CORPUS_MISMATCH")
        if any(not row["source_spans"] for row in corpus):
            raise ValueError("INDEX_MISSING_LOCATORS")
        corpora[key] = corpus
    return corpora


def save(output, manifest, rows):
    measured = [
        r["retrieval_metrics"] for r in rows if r["retrieval_metrics"] is not None
    ]
    metrics = {"n": len(measured)}
    for key in ("hit_at_5", "evidence_recall_at_5", "mrr_at_5"):
        metrics[key] = mean(m[key] for m in measured) if measured else None
    costs = {}
    for field in (
        "load_ms",
        "tokenize_ms",
        "index_ms",
        "score_ms",
        "revalidate_ms",
        "total_ms",
    ):
        values = [r["cost"][field] for r in rows if field in r.get("cost", {})]
        costs[field] = {"n": len(values), "mean": mean(values) if values else None}
    summary = {
        "status": manifest["status"],
        "split": manifest["split"],
        "n": len(rows),
        "evaluation_mode": manifest["evaluation_mode"],
        "completed_n": sum(r["execution"] == "complete" for r in rows),
        "error_n": sum(r["execution"] == "error" for r in rows),
        "not_run_n": sum(r["execution"] == "not_run" for r in rows),
        "retrieval": metrics,
        "cost": costs,
        "model_calls": 0 if any(r["execution"] != "not_run" for r in rows) else None,
        "generation": "not_evaluated: retrieval only",
        "blockers": manifest.get("blockers", []),
        "note": "Draft diagnostics: no reviewed quality results. Small sample only.",
    }
    write_json(output / "manifest.json", manifest)
    write_json(output / "summary.json", summary)
    (output / "results.jsonl").write_text(
        "".join(canonical(row).decode() + "\n" for row in rows), encoding="utf-8"
    )
    (output / "report.md").write_text(
        "# BM25 单路检索\n\n"
        "本报告仅评测检索。draft 只保存原始候选与成本，正式效果值为 null。\n"
        "Hit/Recall/MRR 使用完整原文及位置匹配，无 gold 题不进入分母。\n"
        "小样本需报告分母；未执行问答、引用支持度或事实正确性评测。\n\n```json\n"
        + json.dumps(summary, ensure_ascii=False, indent=2)
        + "\n```\n",
        encoding="utf-8",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions", type=Path, default=ROOT / "data/eval/questions.json"
    )
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "data/sample_docs/manifest.json"
    )
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--run", action="store_true", help="Run against the authorized database"
    )
    parser.add_argument(
        "--draft-diagnostics",
        action="store_true",
        help="Dev raw candidates and cost only",
    )
    args = parser.parse_args(argv)
    output = args.output or ROOT / "artifacts/eval" / ("bm25-" + uuid4().hex[:12])
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "status": "not_run",
        "evaluation_mode": "not_run",
        "split": args.split,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code": code_version(),
        "strategy": {"method": "BM25Okapi", "top_k": 5, **PARAMETERS},
        "preprocessing": PREPROCESSING_VERSION,
        "versions": {name: version(name) for name in ("jieba", "rank-bm25", "numpy")},
        "dictionary_sha256": digest(
            Path(jieba.__file__).with_name("dict.txt").read_bytes()
        ),
        "machine": {
            "system": platform.system(),
            "machine": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "concurrency": 1,
        },
        "index": None,
        "blockers": [],
        "model_calls_enabled": False,
    }
    rows, engine = [], None
    try:
        data = load_dataset(args.questions, args.manifest, args.split)
        manifest.update(
            dataset_sha256=data["dataset_sha256"],
            corpus_manifest_sha256=data["manifest_sha256"],
            corpus_documents=data["documents"],
        )
        (output / "input_questions.json").write_text(
            data["raw_dataset"], encoding="utf-8"
        )
        rows = [
            {
                **q,
                "execution": "not_run",
                "retrieval_metrics": None,
                "items": [],
                "cost": {},
            }
            for q in data["questions"]
        ]
        if args.draft_diagnostics and args.split != "dev":
            raise ValueError("DRAFT_DIAGNOSTICS_DEV_ONLY")
        blockers = list(data["blockers"]) if not args.draft_diagnostics else []
        if not args.run:
            blockers.append("RUN_NOT_REQUESTED")
        if blockers:
            manifest["blockers"] = blockers
            return 2
        if args.mapping is None:
            raise ValueError("KB_MAPPING_REQUIRED")
        settings = load_settings()
        token = os.getenv("EVAL_BEARER_TOKEN")
        if not token:
            raise ValueError("EVAL_TOKEN_REQUIRED")
        mapping_bytes = args.mapping.read_bytes()
        mapping = json.loads(mapping_bytes)
        manifest["mapping_sha256"] = digest(mapping_bytes)
        engine = create_engine(settings.database_url.get_secret_value())
        factory = sessionmaker(engine)
        secret = settings.jwt_secret.get_secret_value()
        actor = authenticate(factory, token, secret)
        corpora = snapshot(factory, actor, mapping, data)
        manifest["index"] = {
            key: {"sha256": digest(canonical(corpus)), "chunks": corpus}
            for key, corpus in corpora.items()
        }
        mode = "draft_diagnostics" if args.draft_diagnostics else "reviewed"
        manifest.update(status="running", evaluation_mode=mode)
        save(output, manifest, rows)
        for row in rows:
            trace = {}
            try:
                actor = authenticate(factory, token, secret)
                items = search(
                    factory,
                    actor,
                    UUID(mapping[row["kb_id"]]),
                    row["question"],
                    5,
                    trace=trace,
                )
                if trace["corpus"] != corpora[row["kb_id"]]:
                    raise ValueError("INDEX_CHANGED")
                by_chunk = {c["chunk_id"]: c for c in trace["corpus"]}
                docs = {
                    d["file_sha256"]: d
                    for d in data["documents"]
                    if d["kb_key"] == row["kb_id"]
                }
                for item in items:
                    source = by_chunk[item.chunk_id]
                    row["items"].append(
                        {
                            **asdict(item),
                            "source_spans": source["source_spans"],
                            "sample_document_id": docs[source["file_sha256"]][
                                "document_id"
                            ],
                        }
                    )
                if mode == "reviewed":
                    row["retrieval_metrics"] = retrieval_metrics(
                        row["gold_evidence"], row["items"]
                    )
                row["execution"] = "complete"
            except Exception as exc:
                row["execution"] = "error"
                row["error_code"] = getattr(
                    exc,
                    "code",
                    "INDEX_CHANGED"
                    if str(exc) == "INDEX_CHANGED"
                    else type(exc).__name__,
                )
            row["cost"] = trace.get("cost", {})
            save(output, manifest, rows)
        manifest["status"] = (
            "completed"
            if all(r["execution"] == "complete" for r in rows)
            else "completed_with_errors"
        )
        return 0 if manifest["status"] == "completed" else 1
    except (Exception, KeyboardInterrupt) as exc:
        safe = {
            "CORPUS_MISMATCH",
            "INDEX_MISSING_LOCATORS",
            "KB_MAPPING_REQUIRED",
            "EVAL_TOKEN_REQUIRED",
            "DRAFT_DIAGNOSTICS_DEV_ONLY",
            "TEST_FREEZE_MISMATCH",
        }
        manifest.update(
            status="blocked",
            blockers=[str(exc) if str(exc) in safe else type(exc).__name__],
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()
        save(output, manifest, rows)
        print(f"{manifest['status']}: {output}")


if __name__ == "__main__":
    raise SystemExit(main())
