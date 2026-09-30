"""One real browser journey from upload to cited answer, with fake models only."""

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
from test_documents import document_env as document_env
from test_ingestion_jobs import job_env as job_env

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_FRONTEND_E2E") != "1", reason="Requires npm ci and Chromium"
)


def test_key_journey_and_authorization(job_env, tmp_path):
    root = Path(__file__).resolve().parents[2]
    _, _, _, _, a, b, _, _, _ = job_env
    npm = shutil.which("npm")
    assert npm, "npm is required"
    # Test-only injection: real auth/permissions/parsing/chunking/SQL/HTTP/worker.
    (tmp_path / "ci_app.py").write_text(
        "from app.main import create_app\n"
        "from app.container_runtime import DemoChat, DemoDecision\n"
        "def create():\n"
        "    app = create_app()\n"
        "    app.state.answer_chat_factory = DemoChat\n"
        "    app.state.agent_decision_factory = DemoDecision\n"
        "    return app\n",
        encoding="utf-8",
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join([str(tmp_path), str(root / "backend")]),
        ACCESS_TOKEN_TTL_MINUTES="30",
        RETRIEVAL_EMBEDDING_BACKEND="fake",
    )
    for key in ("OPENAI_API_KEY", "COHERE_API_KEY"):
        env.pop(key, None)
    output = root / "artifacts/validation/step29"
    output.mkdir(parents=True, exist_ok=True)
    processes = []
    with (
        (output / "keyflow-api.txt").open("w") as api_log,
        (output / "keyflow-worker.txt").open("w") as worker_log,
    ):
        try:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "ci_app:create",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
                cwd=root / "backend",
                env=env,
                stdout=api_log,
                stderr=subprocess.STDOUT,
            )
            processes.append(server)
            deadline = monotonic() + 20
            with httpx.Client(timeout=0.5, trust_env=False) as client:
                while True:
                    assert server.poll() is None, "API startup failed"
                    try:
                        if client.get(base + "/health/live").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    assert monotonic() < deadline, "API startup timed out"
                    Event().wait(0.05)
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-m", "app.worker", "--poll-seconds", "0.1"],
                    cwd=root / "backend",
                    env=env,
                    stdout=worker_log,
                    stderr=subprocess.STDOUT,
                )
            )
            result = subprocess.run(
                [npm, "test"],
                cwd=root / "frontend",
                env=dict(
                    env,
                    E2E_REAL="1",
                    E2E_KEYFLOW="1",
                    E2E_KB=a,
                    E2E_FOREIGN_KB=b,
                    API_PROXY_TARGET=base,
                ),
                capture_output=True,
                text=True,
                timeout=150,
            )
            safe = result.stdout + result.stderr
            for secret in (
                env["JWT_SECRET"],
                *(f"password-for-{u}" for u in ("alice", "bob", "carol")),
            ):
                safe = safe.replace(secret, "[REDACTED]")
            (output / "keyflow-browser.txt").write_text(safe, encoding="utf-8")
            assert result.returncode == 0, safe
            assert all(p.poll() is None for p in processes), "API/worker exited"
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
