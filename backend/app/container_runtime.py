"""Explicit local-container entrypoints; never a fallback for failed real models."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Request
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import URL

from alembic import command
from app.agent.decision import FakeDecisionClient
from app.config import load_settings
from app.http import error_response
from app.llm.fake import FakeChatClient

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_LOCK = 0x5241474445534B


class ContainerConfigurationError(Exception):
    pass


def prepare_environment(*, require_model=False):
    mode = os.environ.get("RAGDESK_MODE")
    if mode not in {"fake", "real"}:
        raise ContainerConfigurationError("RAGDESK_MODE_MUST_BE_fake_OR_real")
    for key in ("POSTGRES_PASSWORD", "JWT_SECRET"):
        if not os.environ.get(key, "").strip():
            raise ContainerConfigurationError(f"{key}_REQUIRED")
    os.environ["DATABASE_URL"] = URL.create(
        "postgresql+psycopg",
        username=os.environ.get("POSTGRES_USER", "ragdesk"),
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ.get("DB_HOST", "db"),
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ.get("POSTGRES_DB", "ragdesk"),
        query={"connect_timeout": "3"},
    ).render_as_string(hide_password=False)
    os.environ["RETRIEVAL_EMBEDDING_BACKEND"] = "fake" if mode == "fake" else "openai"
    if mode == "fake":
        for key in ("OPENAI_API_KEY", "COHERE_API_KEY"):
            os.environ.pop(key, None)
    elif require_model and not os.environ.get("OPENAI_API_KEY", "").strip():
        raise ContainerConfigurationError("OPENAI_API_KEY_REQUIRED_FOR_REAL_MODE")
    try:
        load_settings()
    except RuntimeError as exc:
        # load_settings only lists invalid field names, never their values.
        raise ContainerConfigurationError(str(exc)) from None
    return mode


class DemoChat(FakeChatClient):
    def __init__(self):
        super().__init__({})

    def generate(self, messages, response_schema):
        evidence = json.loads(messages[-1]["content"]).get("untrusted_evidence", [])
        if evidence:
            item = evidence[0]
            excerpt = re.sub(r"\[c\d+\]", "［原文引用标记］", item["text"][:400])
            self.output = {
                "status": "answered",
                "answer": "**FAKE 离线演示：不代表模型效果。**\n\n"
                "仅回显首个授权片段，不判断它是否回答了问题：\n\n"
                + excerpt
                + f"\n\n[{item['citation_id']}]",
                "citation_ids": [item["citation_id"]],
            }
        else:
            self.output = {
                "status": "insufficient_evidence",
                "answer": "FAKE 演示：没有可展示的授权片段。",
                "citation_ids": [],
            }
        return super().generate(messages, response_schema)


class DemoDecision(FakeDecisionClient):
    def __init__(self):
        super().__init__({})

    def decide(self, messages):
        data = json.loads(messages[-1]["content"])
        self.content = {
            "tool_calls": []
            if data["history"]
            else [
                {
                    "id": "demo-search",
                    "name": "search_knowledge",
                    "arguments": {"query": data["original_question"], "top_k": 5},
                }
            ]
        }
        return super().decide(messages)


def alembic_config():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return config


def check_database(engine):
    expected = set(ScriptDirectory.from_config(alembic_config()).get_heads())
    with engine.connect() as connection:
        actual = set(
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars()
        )
        vector = connection.scalar(
            text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')")
        )
    if actual != expected or not vector:
        raise ContainerConfigurationError("DATABASE_MIGRATION_REQUIRED")


def migrate(engine):
    # A session lock covers the separate connection used by Alembic.
    with engine.connect() as connection:
        if not connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": MIGRATION_LOCK}
        ):
            raise ContainerConfigurationError("MIGRATION_ALREADY_RUNNING")
        connection.commit()
        try:
            command.upgrade(alembic_config(), "head")
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK}
            )
            connection.commit()


def create_container_app():
    from app.main import create_app

    mode = prepare_environment(require_model=True)
    app = create_app()
    if mode == "fake":
        app.state.answer_chat_factory = DemoChat
        app.state.agent_decision_factory = DemoDecision

    @app.get("/health/ready")
    def ready(request: Request):
        try:
            check_database(app.state.engine)
        except Exception:
            return error_response(
                request, 503, "DATABASE_NOT_READY", "Database or schema is not ready"
            )
        return {"status": "ready", "mode": mode, "request_id": request.state.request_id}

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["api", "worker", "migrate", "check", "init-demo"]
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    engine = None
    try:
        mode = prepare_environment(require_model=args.action in {"api", "worker"})
        if args.action == "api":
            import uvicorn

            uvicorn.run(
                create_container_app,
                factory=True,
                host="0.0.0.0",
                port=8000,
                access_log=False,
                proxy_headers=False,
            )
            return 0
        if args.action == "worker":
            from app.worker import main as worker_main

            return worker_main(args.arguments)
        if args.action == "init-demo":
            from app.init_demo_users import main as init_main

            return init_main(args.arguments)
        engine = create_engine(
            load_settings().database_url.get_secret_value(), poolclass=pool.NullPool
        )
        if args.action == "migrate":
            migrate(engine)
        check_database(engine)
        print(json.dumps({"status": "ready", "mode": mode}))
        return 0
    except ContainerConfigurationError as exc:
        print(json.dumps({"error_code": str(exc)}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"error_code": "CONTAINER_COMMAND_FAILED"}), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
