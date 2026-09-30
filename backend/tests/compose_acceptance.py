"""Opt-in disposable Compose acceptance. Never uses existing project volumes.

Run from the repository root with the backend Python environment.
Requires Docker, httpx, and network access for locked image builds. No real models.
"""

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default="docker")
    args = parser.parse_args()
    project = "ragdesk-acceptance-" + uuid4().hex[:10]
    output = ROOT / "artifacts/validation/step28" / project
    output.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    db_password, jwt_secret, password = (secrets.token_hex(32) for _ in range(3))
    env_file = output / ".env"
    env_file.write_text(
        f"COMPOSE_PROJECT_NAME={project}\nRAGDESK_MODE=fake\n"
        f"FRONTEND_PORT={port}\nPOSTGRES_PASSWORD={db_password}\n"
        f"JWT_SECRET={jwt_secret}\nOPENAI_API_KEY=\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    env = dict(os.environ)
    # Shell variables take precedence over --env-file. Never inherit real credentials.
    for key in (
        "COMPOSE_PROJECT_NAME",
        "RAGDESK_MODE",
        "FRONTEND_PORT",
        "POSTGRES_PASSWORD",
        "JWT_SECRET",
        "OPENAI_API_KEY",
        "CHAT_MODEL",
        "EMBEDDING_MODEL",
    ):
        env.pop(key, None)
    compose = [
        args.docker,
        "compose",
        "--env-file",
        str(env_file.relative_to(ROOT)),
        "-p",
        project,
    ]
    log = (output / "commands.txt").open("w", encoding="utf-8")
    checks = []

    def safe(value):
        for secret in (db_password, jwt_secret, password):
            value = value.replace(secret, "[REDACTED]")
        return value

    def run(*command, expected=0, input=None, timeout=240):
        result = subprocess.run(
            [*compose, *command],
            cwd=ROOT,
            env=env,
            input=input,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        log.write("COMMAND " + " ".join(command) + "\n" + safe(result.stdout) + "\n")
        log.flush()
        assert expected is None or result.returncode == expected, safe(
            result.stdout[-2200:]
        )
        return result.stdout

    def passed(name):
        checks.append(name)
        print("PASS " + name, flush=True)

    def wait_for(fn, timeout=45):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            value = fn()
            if value:
                return value
            Event().wait(0.3)
        raise AssertionError("Timed out waiting for condition")

    def inspect(service):
        cid = run("ps", "-a", "-q", service).strip()
        result = subprocess.run(
            [args.docker, "inspect", cid],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)[0]

    started = monotonic()
    report = {"mode": "fake", "project": project, "checks": checks, "status": "failed"}
    try:
        run("config", "--quiet")
        run("build", timeout=600)
        passed("locked_images_built")
        run("up", "-d", "--wait", "db")
        run("up", "-d", "--no-deps", "backend")
        probe = (
            "import urllib.request,urllib.error; "
            "r=urllib.request.urlopen('http://127.0.0.1:8000/health/live',timeout=3); "
            "print(r.status)"
        )
        wait_for(
            lambda: (
                run(
                    "exec", "-T", "backend", "python", "-c", probe, expected=None
                ).strip()
                == "200"
            )
        )
        before = run(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "ragdesk",
            "-d",
            "ragdesk",
            "-Atc",
            "SELECT count(*) FROM information_schema.tables WHERE table_name='users'",
        )
        assert before.strip() == "0"
        run(
            "exec",
            "-T",
            "backend",
            "python",
            "-m",
            "app.container_runtime",
            "check",
            expected=1,
        )
        passed("api_starts_without_ddl_and_unmigrated_database_is_not_ready")
        run("--profile", "init", "run", "--rm", "migrate")
        run("--profile", "init", "run", "--rm", "migrate")
        passed("explicit_migration_is_repeatable")
        # Hold the same advisory lock via a separate process; release using stdin.
        holder_code = (
            "from app.container_runtime import prepare_environment,MIGRATION_LOCK;"
            "from app.config import load_settings;"
            "from sqlalchemy import create_engine,text;"
            "prepare_environment();e=create_engine(load_settings().database_url.get_secret_value());"
            "c=e.connect();"
            "c.execute(text('SELECT pg_advisory_lock(:k)'),{'k':MIGRATION_LOCK});"
            "c.commit();print('LOCKED',flush=True);input();c.close();e.dispose()"
        )
        holder = subprocess.Popen(
            [*compose, "exec", "-T", "backend", "python", "-c", holder_code],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "LOCKED"
            conflict = run("--profile", "init", "run", "--rm", "migrate", expected=2)
            assert "MIGRATION_ALREADY_RUNNING" in conflict
        finally:
            holder.communicate(input="\n", timeout=15)
        passed("concurrent_migration_rejected")
        run(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "backend",
            "init-demo",
            "--login-name",
            "compose-demo",
            "--display-name",
            "Compose Demo",
            input=password + "\n" + password + "\n",
        )
        run("up", "-d", "--wait")
        real = run(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "-e",
            "RAGDESK_MODE=real",
            "backend",
            "api",
            expected=2,
        )
        assert "OPENAI_API_KEY_REQUIRED_FOR_REAL_MODE" in real
        passed("real_mode_without_key_fails_without_fake_fallback")
        url = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=url, trust_env=False, timeout=15) as client:
            html = client.get("/")
            assert html.status_code == 200 and '<div id="root">' in html.text
            asset = re.search(r'src="(/assets/[^\"]+\.js)"', html.text).group(1)
            assert client.get(asset).status_code == 200
            assert client.get("/api/health/ready").json()["mode"] == "fake"
            login = client.post(
                "/api/auth/session",
                json={"login_name": "compose-demo", "password": password},
            )
            assert login.status_code == 200
            token = login.json()["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            assert client.get("/api/auth/me").status_code == 200
            kb_response = client.post(
                "/api/knowledge-bases", json={"name": "Compose Demo"}
            )
            assert kb_response.status_code == 201, kb_response.text
            kb = kb_response.json()["id"]
            base = f"/api/knowledge-bases/{kb}"

            def upload(content):
                response = client.post(
                    base + "/documents",
                    files={"file": ("demo.md", content, "text/markdown")},
                )
                assert response.status_code == 202, response.text
                return response.json()

            def job_done(item):
                data = client.get("/api" + item["status_url"]).json()
                assert data["status"] != "failed", data
                return data if data["status"] == "succeeded" else None

            content = "# 演示数据\n\n报销上限 680 元，7 天内提交。\n".encode()
            uploaded = upload(content)
            wait_for(lambda: job_done(uploaded))
            doc_path = base + "/documents/" + uploaded["document_id"]
            assert client.get(doc_path + "/raw").content == content
            answers = []
            for mode in ("rag", "agent"):
                response = client.post(
                    base + "/answers",
                    json={"question": "报销上限是多少？", "mode": mode},
                )
                assert response.status_code == 200, response.text
                answer = response.json()
                assert answer["status"] == "answered" and "FAKE" in answer["answer"]
                assert answer["citations"]
                for citation in answer["citations"]:
                    assert (
                        client.get("/api" + citation["source_path"]).status_code == 200
                    )
                answers.append(answer)
            (output / "fake-answers.json").write_text(
                json.dumps(answers, ensure_ascii=False, indent=2)
            )
            passed("static_frontend_proxy_login_upload_worker_rag_agent_sources")
            for service in ("db", "backend", "worker"):
                assert not inspect(service)["HostConfig"]["PortBindings"]
            ports = inspect("frontend")["HostConfig"]["PortBindings"]
            assert ports == {
                "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]
            }
            passed("only_loopback_frontend_port_published")
            run("stop", "worker")
            queued = upload(content + b"\nQueued before recreation.\n")
            assert (
                client.get("/api" + queued["status_url"]).json()["status"] == "queued"
            )
            run("down")
            run("up", "-d", "--wait")
            assert client.get("/api/auth/me").status_code == 200
            assert client.get(doc_path + "/raw").content == content
            wait_for(lambda: job_done(queued))
            passed(
                "containers_recreated_original_file_and_database_retained_queued_job_completed"
            )
            backend_id = inspect("backend")["Id"]
            worker_restarts = inspect("worker")["RestartCount"]
            run("stop", "db")
            assert client.get("/api/health/ready").status_code == 503
            wait_for(lambda: inspect("worker")["RestartCount"] > worker_restarts)
            run("up", "-d", "--wait", "db")
            wait_for(lambda: client.get("/api/health/ready").status_code == 200)
            assert inspect("backend")["Id"] == backend_id
            recovered = upload(content + b"\nAfter database recovery.\n")
            wait_for(lambda: job_done(recovered))
            passed(
                "database_outage_api_reconnect_same_process_worker_restart_and_new_job_completed"
            )
        logs = run("logs", "--no-color", "backend", "worker")
        assert all(
            secret not in logs for secret in (db_password, jwt_secret, password, token)
        )
        passed("application_logs_do_not_contain_test_secrets")
        report["status"] = "passed"
    finally:
        report["duration_seconds"] = round(monotonic() - started, 2)
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        # Only this script's freshly generated UUID project is removed.
        try:
            run("down", "-v", "--remove-orphans")
        finally:
            log.close()
        print("Report: " + str(output / "report.json"), flush=True)


if __name__ == "__main__":
    main()
