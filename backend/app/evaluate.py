"""Save the exact-vector baseline. Default: dev preflight, no model calls."""

import argparse
import json
import os
import platform
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import load_settings
from app.evaluation.data import ROOT, canonical, digest, load_dataset
from app.evaluation.metrics import summarize
from app.evaluation.runtime import (
    RecordedClient,
    authenticate,
    bind_index,
    run_question,
)
from app.llm.openai import OpenAIChatClient, OpenAIEmbeddingClient
from app.services.answers import ContextBudget
from app.services.ingest import EmbeddingProfile


def code_version():
    files = sorted((ROOT / "backend/app").rglob("*.py")) + [
        ROOT / "backend/pyproject.toml",
        ROOT / "backend/uv.lock",
    ]
    hashes = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in files}
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError):
        head, dirty = None, None
    return {
        "git_commit": head,
        "git_dirty": dirty,
        "source_files": hashes,
        "source_tree_sha256": digest(canonical(hashes)),
        "python": platform.python_version(),
    }


def write_json(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def report(output, manifest, rows):
    summary = summarize(rows)
    summary.update(
        run_status=manifest["status"],
        execution_mode=manifest["execution_mode"],
        blockers=manifest.get("blockers", []),
        split=manifest["split"],
    )
    write_json(output / "manifest.json", manifest)
    write_json(output / "summary.json", summary)
    with (output / "results.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(canonical(row).decode("utf-8") + "\n")
    text = [
        "# 向量检索基线",
        "",
        f"运行状态：{manifest['status']}；split：{manifest['split']}；样本量：{len(rows)}。",
        "",
        "效果值为 null 表示没有可报告的测量，不能当成 0。技术错误不算拒答。",
        "Evidence Recall@5 为逐题宏平均；严格匹配完整原文及位置。",
        "引用支持结论和事实正确性待独立人工复核；小样本不代表总体效果。",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    (output / "report.md").write_text("\n".join(text), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions", type=Path, default=ROOT / "data/eval/questions.json"
    )
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "data/sample_docs/manifest.json"
    )
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument(
        "--mapping", type=Path, help="JSON object: logical KB key -> database UUID"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--run-real",
        action="store_true",
        help="Explicitly permit real model calls on reviewed samples",
    )
    args = parser.parse_args(argv)
    output = args.output or ROOT / "artifacts/eval" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "status": "not_run",
        "execution_mode": "not_run",
        "split": args.split,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code": code_version(),
        "strategy": {"metric": "cosine_distance", "search": "exact", "top_k": 5},
        "context_budget": asdict(ContextBudget()),
        "model_config": None,
        "index": None,
        "blockers": [],
        "raw_output_scope": (
            "Adapter results and HTTP response bodies; known API key redacted. "
            "No request headers; network failures may have no response."
        ),
    }
    rows, engine = [], None
    try:
        data = load_dataset(args.questions, args.manifest, args.split)
        manifest.update(
            dataset_sha256=data["dataset_sha256"],
            corpus_manifest_sha256=data["manifest_sha256"],
            corpus_documents=data["documents"],
            selected_n=len(data["questions"]),
        )
        (output / "input_questions.json").write_text(
            data["raw_dataset"], encoding="utf-8"
        )
        rows = [
            {
                "id": q["id"],
                "question": q["question"],
                "kb_id": q["kb_id"],
                "split": q["split"],
                "answerable": q["answerable"],
                "category": q["category"],
                "review_status": q["review_status"],
                "execution": "not_run",
                "retrieval_metrics": None,
                "answer_status": None,
                "citation_valid": None,
                "retrieval_ms": None,
                "total_ms": None,
                "calls": [],
            }
            for q in data["questions"]
        ]
        blockers = list(data["blockers"])
        if not args.run_real:
            blockers.append("REAL_RUN_NOT_REQUESTED")
        if blockers:
            manifest["blockers"] = blockers
            for row in rows:
                row["not_run_reasons"] = blockers
            return 2
        if args.mapping is None:
            raise ValueError("KB_MAPPING_REQUIRED")
        settings = load_settings()
        if settings.openai_api_key is None or not os.getenv("EVAL_BEARER_TOKEN"):
            raise ValueError("REAL_MODEL_KEY_AND_EVAL_TOKEN_REQUIRED")
        if settings.retrieval_embedding_backend != "openai":
            raise ValueError("REAL_INDEX_REQUIRED")
        mapping_raw = args.mapping.read_bytes()
        mapping = json.loads(mapping_raw)
        manifest["mapping_sha256"] = digest(mapping_raw)
        profile = EmbeddingProfile(
            provider="openai",
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
        engine = create_engine(settings.database_url.get_secret_value())
        factory = sessionmaker(engine)
        token, secret = (
            os.environ["EVAL_BEARER_TOKEN"],
            settings.jwt_secret.get_secret_value(),
        )
        user_id = authenticate(factory, token, secret)
        keys = {q["kb_id"] for q in data["questions"]}
        index = bind_index(factory, user_id, mapping, data["documents"], keys, profile)
        manifest["index"] = {
            key: value for key, value in index.items() if key != "chunk_map"
        }
        manifest["model_config"] = {
            "embedding_config_id": profile.config_id,
            "chat_model": settings.chat_model,
            "max_attempts": settings.model_max_attempts,
            "connect_timeout_seconds": settings.model_connect_timeout_seconds,
            "read_timeout_seconds": settings.model_read_timeout_seconds,
        }
        options = {
            "api_key": settings.openai_api_key.get_secret_value(),
            "max_attempts": settings.model_max_attempts,
            "connect_timeout": settings.model_connect_timeout_seconds,
            "read_timeout": settings.model_read_timeout_seconds,
        }

        def embedding_factory():
            return RecordedClient(
                OpenAIEmbeddingClient,
                model=profile.model,
                dimensions=profile.dimensions,
                **options,
            )

        def chat_factory():
            return RecordedClient(
                OpenAIChatClient,
                model=settings.chat_model,
                max_completion_tokens=ContextBudget().output_tokens,
                **options,
            )

        def index_check():
            actor = authenticate(factory, token, secret)
            return bind_index(
                factory, actor, mapping, data["documents"], keys, profile
            )["sha256"]

        manifest.update(status="running", execution_mode="real")
        report(output, manifest, rows)
        for i, q in enumerate(data["questions"]):
            rows[i] = run_question(
                q,
                factory,
                user_id,
                UUID(mapping[q["kb_id"]]),
                profile,
                embedding_factory,
                chat_factory,
                index,
                index_check,
                execution_mode="real",
            )
            report(output, manifest, rows)
        manifest["status"] = (
            "completed"
            if all(r["execution"] == "complete" for r in rows)
            else "completed_with_errors"
        )
        return 0 if manifest["status"] == "completed" else 1
    except (Exception, KeyboardInterrupt) as exc:
        manifest["status"] = (
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "blocked"
        )
        # Configuration/database errors may contain secrets; retain safe codes only.
        safe_codes = {
            "KB_MAPPING_REQUIRED",
            "REAL_MODEL_KEY_AND_EVAL_TOKEN_REQUIRED",
            "REAL_INDEX_REQUIRED",
            "CORPUS_MISMATCH",
            "INDEX_CONFIG_MISMATCH",
            "INDEX_MISSING_VECTORS_OR_LOCATORS",
            "GOLD_NOT_FOUND_IN_PARSED_SECTION",
            "TEST_FREEZE_MISMATCH",
            "DUPLICATE_ID",
        }
        manifest["blockers"] = [
            str(exc) if str(exc) in safe_codes else type(exc).__name__
        ]
        for row in rows:
            if row["execution"] == "not_run":
                row["not_run_reasons"] = manifest["blockers"]
        return 2
    finally:
        if engine is not None:
            engine.dispose()
        report(output, manifest, rows)
        print(f"{manifest['status']}: {output}")


if __name__ == "__main__":
    raise SystemExit(main())
