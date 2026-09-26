"""Compare the same RRF20 candidates with and without optional reranking."""

import argparse
import json
import os
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from uuid import UUID, uuid4

import jieba
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import load_settings
from app.evaluate import code_version, write_json
from app.evaluation.data import ROOT, canonical, digest, load_dataset
from app.evaluation.rerank import METHODS, run_question, summarize
from app.evaluation.runtime import RecordedClient, authenticate, bind_index
from app.llm.fake import FakeEmbeddingClient
from app.llm.openai import OpenAIEmbeddingClient
from app.retrieval.bm25 import PARAMETERS, PREPROCESSING_VERSION
from app.retrieval.reranker import CohereReranker, FakeReranker
from app.services.ingest import EmbeddingProfile


def save(output, manifest, rows):
    summary = {
        **summarize(rows),
        "status": manifest["status"],
        "mode": manifest["mode"],
        "blockers": manifest["blockers"],
        "split": manifest["split"],
    }
    write_json(output / "manifest.json", manifest)
    write_json(output / "summary.json", summary)
    (output / "results.jsonl").write_text(
        "".join(canonical(row).decode() + "\n" for row in rows), encoding="utf-8"
    )
    (output / "report.md").write_text(
        "# RRF / RRF+rerank 配对比较\n\n"
        "仅真实模型、已复核且两组成功未降级的有 gold 题进入配对指标分母。\n"
        "fake 仅验证流程；null 表示未测，不能解读为没有提升。\n"
        "正/零/负差值逐项报告，不自动推断总体优劣。\n"
        "共用 RRF20 基线；总耗时包含重排及授权复核，增量耗时单列。\n"
        "计费 search_units 不等于金额。\n\n```json\n"
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run-real", action="store_true")
    mode.add_argument("--fake-diagnostics", action="store_true")
    parser.add_argument("--allow-degraded", action="store_true")
    args = parser.parse_args(argv)
    output = args.output or ROOT / "artifacts/eval" / ("rerank-" + uuid4().hex[:12])
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "status": "not_run",
        "mode": "not_run",
        "split": args.split,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code": code_version(),
        "strategy": {
            "candidate_limit": 20,
            "rrf_k": 60,
            "top_k": 5,
            "vector": "exact_cosine_distance",
            "bm25": PARAMETERS,
            "preprocessing": PREPROCESSING_VERSION,
        },
        "allow_degraded": args.allow_degraded,
        "index": None,
        "blockers": [],
        "versions": {name: version(name) for name in ("jieba", "rank-bm25", "numpy")},
        "dictionary_sha256": digest(
            Path(jieba.__file__).with_name("dict.txt").read_bytes()
        ),
        "model_config": None,
        "reranker_config": {
            "provider": "fake" if args.fake_diagnostics else "cohere",
            "model": "reverse-index-v1" if args.fake_diagnostics else "rerank-v3.5",
            "baseline_enabled": False,
            "experiment_enabled": True,
            "default_enabled": False,
            "max_attempts": 1,
            "max_tokens_per_doc": 4096,
        },
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
                "comparison_eligible": False,
                "items": {m: [] for m in METHODS},
                "metrics": {m: None for m in METHODS},
            }
            for q in data["questions"]
        ]
        if args.fake_diagnostics and args.split != "dev":
            raise ValueError("FAKE_DIAGNOSTICS_DEV_ONLY")
        blockers = [] if args.fake_diagnostics else list(data["blockers"])
        if not args.run_real and not args.fake_diagnostics:
            blockers.append("RUN_NOT_REQUESTED")
        if args.run_real and not os.getenv("OPENAI_API_KEY"):
            blockers.append("REAL_MODEL_KEY_REQUIRED")
        if args.run_real and not os.getenv("COHERE_API_KEY"):
            blockers.append("RERANK_API_KEY_REQUIRED")
        if blockers:
            manifest["blockers"] = blockers
            return 2
        if args.mapping is None:
            raise ValueError("KB_MAPPING_REQUIRED")
        settings = load_settings()
        token = os.getenv("EVAL_BEARER_TOKEN")
        if not token:
            raise ValueError("EVAL_TOKEN_REQUIRED")
        if args.run_real and settings.retrieval_embedding_backend != "openai":
            raise ValueError("REAL_INDEX_REQUIRED")
        profile = (
            EmbeddingProfile.fake()
            if args.fake_diagnostics
            else EmbeddingProfile(
                provider="openai",
                model=settings.embedding_model,
                dimensions=settings.embedding_dimensions,
            )
        )
        execution_mode = "fake_diagnostics" if args.fake_diagnostics else "real"
        mapping_bytes = args.mapping.read_bytes()
        mapping = json.loads(mapping_bytes)
        manifest["mapping_sha256"] = digest(mapping_bytes)
        engine = create_engine(settings.database_url.get_secret_value())
        factory = sessionmaker(engine)
        secret = settings.jwt_secret.get_secret_value()
        actor = authenticate(factory, token, secret)
        keys = {q["kb_id"] for q in data["questions"]}
        index = bind_index(factory, actor, mapping, data["documents"], keys, profile)
        manifest["index"] = {k: v for k, v in index.items() if k != "chunk_map"}
        manifest["model_config"] = {
            "embedding_config_id": profile.config_id,
            "max_attempts": settings.model_max_attempts,
            "connect_timeout_seconds": settings.model_connect_timeout_seconds,
            "read_timeout_seconds": settings.model_read_timeout_seconds,
        }

        def embedding_factory():
            if args.fake_diagnostics:
                return FakeEmbeddingClient()
            return RecordedClient(
                OpenAIEmbeddingClient,
                api_key=settings.openai_api_key.get_secret_value(),
                model=profile.model,
                dimensions=profile.dimensions,
                max_attempts=settings.model_max_attempts,
                connect_timeout=settings.model_connect_timeout_seconds,
                read_timeout=settings.model_read_timeout_seconds,
            )

        manifest["reranker_config"].update(
            connect_timeout_seconds=settings.rerank_connect_timeout_seconds,
            read_timeout_seconds=settings.rerank_read_timeout_seconds,
        )

        def reranker_factory():
            if args.fake_diagnostics:
                return FakeReranker()
            return CohereReranker(
                settings.cohere_api_key.get_secret_value(),
                model=settings.rerank_model,
                connect_timeout=settings.rerank_connect_timeout_seconds,
                read_timeout=settings.rerank_read_timeout_seconds,
            )

        def index_check():
            actor = authenticate(factory, token, secret)
            return bind_index(
                factory, actor, mapping, data["documents"], keys, profile
            )["sha256"]

        manifest.update(status="running", mode=execution_mode)
        save(output, manifest, rows)
        for i, q in enumerate(data["questions"]):
            rows[i] = run_question(
                q,
                factory,
                actor,
                UUID(mapping[q["kb_id"]]),
                profile,
                embedding_factory,
                index,
                index_check,
                mode=execution_mode,
                reranker_factory=reranker_factory,
                allow_degraded=args.allow_degraded,
            )
            save(output, manifest, rows)
        manifest["status"] = (
            "completed"
            if all(r["execution"] == "complete" for r in rows)
            else "completed_with_errors"
        )
        return 0 if manifest["status"] == "completed" else 1
    except (Exception, KeyboardInterrupt) as exc:
        safe = {
            "FAKE_DIAGNOSTICS_DEV_ONLY",
            "KB_MAPPING_REQUIRED",
            "EVAL_TOKEN_REQUIRED",
            "REAL_INDEX_REQUIRED",
            "CORPUS_MISMATCH",
            "INDEX_CONFIG_MISMATCH",
            "INDEX_MISSING_VECTORS_OR_LOCATORS",
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
