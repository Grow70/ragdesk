"""Opt-in real browser/API/worker lifecycle, disposable DB and fake model only."""

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


def test_real_document_management(job_env):
    root = Path(__file__).resolve().parents[2]
    _, _, _, _, a, b, _, _, _ = job_env
    npm = shutil.which("npm")
    assert npm and (root / "frontend/node_modules/.bin/playwright").exists()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    env = dict(os.environ, ACCESS_TOKEN_TTL_MINUTES="30")
    for key in ("OPENAI_API_KEY", "COHERE_API_KEY"):
        env.pop(key, None)
    assert env["RETRIEVAL_EMBEDDING_BACKEND"] == "fake"
    output = root / "artifacts/validation/step26b"
    output.mkdir(parents=True, exist_ok=True)
    processes = []
    with (
        (output / "real-api.txt").open("w") as api_log,
        (output / "real-worker.txt").open("w") as worker_log,
    ):
        try:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:create_app",
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
                    assert server.poll() is None, "API failed to start"
                    try:
                        if client.get(base_url + "/health/live").status_code == 200:
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
                    E2E_DOCUMENTS="1",
                    API_PROXY_TARGET=base_url,
                    E2E_KB=a,
                    E2E_FOREIGN_KB=b,
                ),
                capture_output=True,
                text=True,
                timeout=150,
            )
            safe_output = result.stdout + result.stderr
            for secret in (
                env["JWT_SECRET"],
                "password-for-alice",
                "password-for-carol",
            ):
                safe_output = safe_output.replace(secret, "[REDACTED]")
            (output / "real-browser.txt").write_text(safe_output, encoding="utf-8")
            assert result.returncode == 0, safe_output
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
