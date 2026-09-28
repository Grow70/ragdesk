"""Step 25: paired dev evaluation. Default is preflight with zero API calls."""

import argparse
import json
import os
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.contracts import ToolLimits
from app.config import load_settings
from app.evaluate import code_version, write_json
from app.evaluation.agent_comparison import (
    CapturedChat,
    CapturedDecision,
    CapturedEmbedding,
    ResponseCapture,
    run_arm,
)
from app.evaluation.agent_metrics import ARMS, summarize, worksheet
from app.evaluation.data import ROOT, canonical, digest, load_dataset
from app.evaluation.runtime import authenticate, bind_index
from app.llm.budget import RoundLimits
from app.services.answers import ContextBudget
from app.services.ingest import EmbeddingProfile
from app.services.traces import load_settings as load_prices


def redact(value):
    # Evaluation output may contain provider-echoed secrets. No headers are captured.
    secrets = [
        os.getenv(k)
        for k in ("OPENAI_API_KEY", "JWT_SECRET", "EVAL_BEARER_TOKEN", "DATABASE_URL")
    ]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def save(output, manifest, rows):
    # Round-trip UUID/dataclass outputs to the exact artifact representation first.
    rows = redact(json.loads(canonical(rows)))
    raw = b"".join(canonical(row) + b"\n" for row in rows)
    manifest["results_sha256"] = digest(raw)
    (output / "results.jsonl").write_bytes(raw)
    write_json(output / "manifest.json", redact(manifest))
    write_json(output / "review.template.json", worksheet(rows))
    summary = summarize(rows, mode=manifest["mode"])
    write_report(output, manifest, summary, "report")


def write_report(output, manifest, summary, name):
    write_json(output / f"{name}.json", summary)
    content = [
        "# 固定 RAG / Agentic RAG 配对评测",
        "",
        f"状态：{manifest['status']}；模式：{manifest['mode']}；选题数：{summary['selected_n']}。",
        f"阻塞项：{', '.join(manifest.get('blockers', [])) or '无'}。",
        "",
        "null 表示未知或未运行；错误、澄清、资料不足分别统计。",
        "正确且有证据支持的比例必须独立人工复核；引用 ID 合法不能证明答案正确。",
        "成功/失败轨迹按 results.jsonl 中的题号查阅，少于三例不补造。",
        "简单事实优先固定 RAG；需要改写/补读的问题可作为 Agent 候选。实际收益待复核。",
        "当前不据此默认开启 Agent；模糊表述尚无独立人工标签。",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    (output / f"{name}.md").write_text("\n".join(content), encoding="utf-8")


def review_run(directory, review_path):
    manifest = json.loads((directory / "manifest.json").read_text())
    raw = (directory / "results.jsonl").read_bytes()
    if digest(raw) != manifest["results_sha256"]:
        raise ValueError("RUN_OUTPUT_CHANGED")
    rows = [json.loads(line) for line in raw.splitlines()]
    reviews = json.loads(review_path.read_text(encoding="utf-8"))
    summary = summarize(rows, mode=manifest["mode"], reviews=reviews)
    summary["review_file_sha256"] = digest(review_path.read_bytes())
    # Never overwrite the original run or prior review reports.
    name = "reviewed-" + uuid4().hex[:8]
    write_json(directory / f"{name}.annotations.json", reviews)
    write_report(directory, manifest, summary, name)
    print(f"{directory / (name + '.md')}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions", type=Path, default=ROOT / "data/eval/questions.json"
    )
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "data/sample_docs/manifest.json"
    )
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-real", action="store_true")
    parser.add_argument("--max-pairs", type=int, default=15, choices=range(1, 16))
    parser.add_argument("--review-run", type=Path)
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args(argv)
    if args.review_run:
        if not args.reviews or args.run_real:
            parser.error("--review-run requires --reviews and forbids --run-real")
        return review_run(args.review_run, args.reviews)
    if args.reviews:
        parser.error("--reviews requires --review-run")
    output = args.output or ROOT / "artifacts/eval" / ("agent-" + uuid4().hex)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "status": "not_run",
        "mode": "not_run",
        "split": "dev",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code": code_version(),
        "blockers": [],
        "model_config": None,
        "index": None,
        "retrieval": {
            "strategy": "vector",
            "metric": "cosine_distance",
            "search": "exact",
            "fixed_top_k": 5,
            "agent_top_k_default": 5,
            "agent_top_k_max": 20,
        },
        "context_budget": asdict(ContextBudget()),
        "agent_limits": asdict(RoundLimits()),
        "agent_tool_limits": ToolLimits().model_dump(),
        "confounds": [
            "agent sees previews and only latest evidence; fixed sees full chunks",
            "agent may choose top_k within shared service limits",
            "fixed has per-call timeouts, agent also has 60s round deadline",
        ],
        "order": "alternate first arm; sequential; no warmup or tuning",
        "raw_output_scope": (
            "parsed provider responses returned by existing HTTP adapter, answers, "
            "evidence, traces; no headers/prompts; failed transport/JSON bodies "
            "unavailable; late deadline results excluded"
        ),
        "max_network_attempts": args.max_pairs * 12,
    }
    rows, engine = [], None
    try:
        data = load_dataset(args.questions, args.manifest, "dev")
        questions = data["questions"][: args.max_pairs]
        manifest.update(
            dataset_sha256=data["dataset_sha256"],
            corpus_manifest_sha256=data["manifest_sha256"],
            corpus_documents=data["documents"],
            selected_ids=[q["id"] for q in questions],
            category_counts=dict(Counter(q["category"] for q in questions)),
        )
        (output / "input_questions.json").write_text(
            data["raw_dataset"], encoding="utf-8"
        )
        rows = [
            {
                "sample": q,
                **{arm: {"execution": "not_run", "result": None} for arm in ARMS},
            }
            for q in questions
        ]
        blockers = list(data["blockers"])
        if not args.run_real:
            blockers.append("REAL_RUN_NOT_REQUESTED")
        blockers.extend(
            "MISSING_" + key
            for key in (
                "DATABASE_URL",
                "JWT_SECRET",
                "OPENAI_API_KEY",
                "EVAL_BEARER_TOKEN",
            )
            if not os.getenv(key)
        )
        if args.mapping is None:
            blockers.append("KB_MAPPING_REQUIRED")
        if blockers:
            manifest["blockers"] = blockers
            return 2
        settings, prices = load_settings(), load_prices().prices
        if settings.retrieval_embedding_backend != "openai":
            raise ValueError("REAL_INDEX_REQUIRED")
        # Pin models to the existing documented adapter capabilities.
        if (
            settings.chat_model != "gpt-4.1-mini-2025-04-14"
            or settings.embedding_model != "text-embedding-3-small"
        ):
            raise ValueError("UNVERIFIED_MODEL_CONFIGURATION")
        mapping_raw = args.mapping.read_bytes()
        mapping = json.loads(mapping_raw)
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
        keys = {q["kb_id"] for q in questions}
        index = bind_index(factory, user_id, mapping, data["documents"], keys, profile)
        manifest.update(
            mapping_sha256=digest(mapping_raw),
            index={k: v for k, v in index.items() if k != "chunk_map"},
            model_config={
                "profile": asdict(profile),
                "chat_model": settings.chat_model,
                "max_attempts": settings.model_max_attempts,
                "connect_timeout_seconds": settings.model_connect_timeout_seconds,
                "read_timeout_seconds": settings.model_read_timeout_seconds,
                "max_completion_tokens": ContextBudget().output_tokens,
            },
            prices={k: v.model_dump(mode="json") for k, v in prices.items()},
            status="running",
            mode="real",
        )
        options = {
            "api_key": settings.openai_api_key.get_secret_value(),
            "max_attempts": settings.model_max_attempts,
            "connect_timeout": settings.model_connect_timeout_seconds,
            "read_timeout": settings.model_read_timeout_seconds,
        }

        def index_check():
            actor = authenticate(factory, token, secret)
            return bind_index(
                factory, actor, mapping, data["documents"], keys, profile
            )["sha256"]

        save(output, manifest, rows)
        for i, row in enumerate(rows):
            order = ARMS if i % 2 == 0 else tuple(reversed(ARMS))
            row["order"] = order
            for arm in order:
                capture = ResponseCapture()
                row[arm] = run_arm(
                    arm,
                    row["sample"]["question"],
                    factory=factory,
                    user_id=user_id,
                    kb_id=UUID(mapping[row["sample"]["kb_id"]]),
                    profile=profile,
                    embedding_factory=lambda: CapturedEmbedding(
                        capture=capture,
                        model=profile.model,
                        dimensions=profile.dimensions,
                        **options,
                    ),
                    chat_factory=lambda: CapturedChat(
                        capture=capture,
                        model=settings.chat_model,
                        max_completion_tokens=ContextBudget().output_tokens,
                        **options,
                    ),
                    decision_factory=lambda: CapturedDecision(
                        capture=capture,
                        model=settings.chat_model,
                        max_completion_tokens=ContextBudget().output_tokens,
                        **options,
                    ),
                    index_check=index_check,
                    index_sha256=index["sha256"],
                    prices=prices,
                    capture=capture,
                )
                save(output, manifest, rows)
                if row[arm]["execution"] == "invalidated":
                    raise ValueError("INDEX_OR_AUTH_CHANGED")
                if row[arm].get("trace", {}).get("trace_incomplete"):
                    raise ValueError("INCOMPLETE_TRACE_STOPPED_RUN")
        manifest["status"] = (
            "completed"
            if all(row[a]["execution"] == "complete" for row in rows for a in ARMS)
            else "completed_with_errors"
        )
        return 0 if manifest["status"] == "completed" else 1
    except (Exception, KeyboardInterrupt) as exc:
        manifest["status"] = (
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "blocked"
        )
        allowed = {
            "REAL_INDEX_REQUIRED",
            "UNVERIFIED_MODEL_CONFIGURATION",
            "INDEX_CHANGED",
            "INDEX_OR_AUTH_CHANGED",
            "INCOMPLETE_TRACE_STOPPED_RUN",
            "CORPUS_MISMATCH",
            "INDEX_CONFIG_MISMATCH",
        }
        manifest["blockers"] = [str(exc) if str(exc) in allowed else type(exc).__name__]
        return 2
    finally:
        if engine is not None:
            engine.dispose()
        save(output, manifest, rows)
        print(f"{manifest['status']}: {output}")


if __name__ == "__main__":
    raise SystemExit(main())
