"""Opt-in Chromium + Vite + real FastAPI/JWT/Argon2 + disposable PostgreSQL."""

import os
import secrets
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from time import monotonic

import httpx
import pytest
from sqlalchemy.orm import Session
from test_retrieval import SECRET
from test_retrieval import retrieval_db as retrieval_db

from app.models import KnowledgeBase, User
from app.services.auth import create_access_token, hash_password

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_FRONTEND_E2E") != "1",
    reason="Set RUN_FRONTEND_E2E=1 after npm ci and playwright install chromium",
)


def test_real_frontend_authentication(retrieval_db):
    root = Path(__file__).resolve().parents[2]
    frontend = root / "frontend"
    npm = shutil.which("npm")
    assert npm and (frontend / "node_modules/.bin/playwright").exists(), (
        "Install Node dependencies before enabling the browser integration test"
    )
    engine, ids = retrieval_db
    password = secrets.token_urlsafe(24)
    with Session(engine) as session:
        user = session.get(User, ids["alice"])
        user.display_name = "演示成员"
        user.password_hash = hash_password(password)
        for key, name in (
            ("a", "A · 团队资料"),
            ("b", "B · 隔离资料"),
            ("empty", "个人演示空间"),
        ):
            session.get(KnowledgeBase, ids[key]).name = name
        session.commit()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    env = dict(os.environ, ACCESS_TOKEN_TTL_MINUTES="1")
    for key in ("OPENAI_API_KEY", "COHERE_API_KEY"):
        env.pop(key, None)
    output = root / "artifacts/validation/step26a"
    output.mkdir(parents=True, exist_ok=True)
    with (output / "backend.txt").open("w") as log:
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
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = monotonic() + 20
            with httpx.Client(timeout=0.5, trust_env=False) as client:
                while True:
                    assert server.poll() is None, "Backend startup failed; see safe log"
                    try:
                        if client.get(base_url + "/health/live").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    assert monotonic() < deadline, "Backend startup timed out"
                    Event().wait(0.05)
            expired = create_access_token(
                ids["alice"],
                SECRET,
                1,
                issued_at=datetime.now(timezone.utc) - timedelta(minutes=2),
            )
            browser_env = dict(
                env,
                E2E_REAL="1",
                API_PROXY_TARGET=base_url,
                E2E_PASSWORD=password,
                E2E_EXPIRED_TOKEN=expired,
                E2E_FOREIGN_KB=str(ids["b"]),
            )
            result = subprocess.run(
                [npm, "test"],
                cwd=frontend,
                env=browser_env,
                capture_output=True,
                text=True,
                timeout=150,
            )
            # Playwright trace/video disabled; no passwords or bearer headers saved.
            safe_output = result.stdout + result.stderr
            for secret in (password, expired, SECRET):
                safe_output = safe_output.replace(secret, "[REDACTED]")
            (output / "real-browser.txt").write_text(safe_output, encoding="utf-8")
            assert result.returncode == 0, safe_output
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
