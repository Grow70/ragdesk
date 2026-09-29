"""Real browser/API/database round trip; models are injected only by this test."""

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from threading import Event
from time import monotonic

import httpx
import pytest
from sqlalchemy.orm import Session
from test_retrieval import _seed
from test_retrieval import retrieval_db as retrieval_db

from app.models import KnowledgeBase

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_FRONTEND_E2E") != "1", reason="Requires npm ci and Chromium"
)


def test_real_single_question_ui(retrieval_db, tmp_path):
    root = Path(__file__).resolve().parents[2]
    engine, ids = retrieval_db
    _seed(
        engine,
        ids["a"],
        "演示数据：报销上限 680 元，7 天内提交。",
        [1.0] + [0.0] * 1535,
    )
    with Session(engine) as session:
        session.get(KnowledgeBase, ids["a"]).name = "A · 演示报销"
        session.get(KnowledgeBase, ids["empty"]).name = "空库"
        session.commit()
    # No fake HTTP response or production fake-chat flag: a test-only app factory.
    (tmp_path / "question_test_app.py").write_text(
        """
from test_agent_http import agent_app
from app.llm.fake import FakeChatClient

def create():
    return agent_app(FakeChatClient({
        "status": "answered",
        "answer": "## 报销要求\\n\\n上限 **680 元**，7 天内提交。[c1]",
        "citation_ids": ["c1"],
    }))
""",
        encoding="utf-8",
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(
            [str(tmp_path), str(root / "backend/tests"), str(root / "backend")]
        ),
    )
    for key in ("OPENAI_API_KEY", "COHERE_API_KEY"):
        env.pop(key, None)
    output = root / "artifacts/validation/step27"
    output.mkdir(parents=True, exist_ok=True)
    npm = shutil.which("npm")
    assert npm
    with (output / "real-api.txt").open("w") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "question_test_app:create",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=root / "backend",
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            url = f"http://127.0.0.1:{port}"
            deadline = monotonic() + 20
            with httpx.Client(timeout=0.5, trust_env=False) as client:
                while True:
                    assert server.poll() is None, "Test API failed to start"
                    try:
                        if client.get(url + "/health/live").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    assert monotonic() < deadline, "Test API startup timed out"
                    Event().wait(0.05)
            result = subprocess.run(
                [npm, "test"],
                cwd=root / "frontend",
                env=dict(
                    env,
                    E2E_REAL="1",
                    E2E_QUESTION="1",
                    API_PROXY_TARGET=url,
                    E2E_EMPTY_KB=str(ids["empty"]),
                    E2E_FOREIGN_KB=str(ids["b"]),
                ),
                capture_output=True,
                text=True,
                timeout=150,
            )
            safe = (
                (result.stdout + result.stderr)
                .replace(env["JWT_SECRET"], "[REDACTED]")
                .replace("test-password", "[REDACTED]")
            )
            (output / "real-browser.txt").write_text(safe, encoding="utf-8")
            assert result.returncode == 0, safe
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
