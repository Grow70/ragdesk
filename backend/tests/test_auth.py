"""Authentication against an isolated disposable PostgreSQL database."""

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import jwt
import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic import command
from app.init_demo_users import main as init_demo_user
from app.main import create_app
from app.models import User
from app.services.auth import create_access_token

_SECRET = "test-jwt-secret-with-at-least-32-bytes"
_PASSWORD = "demo-password-12345"


@pytest.fixture
def auth_database(monkeypatch):
    admin_url = os.getenv("TEST_POSTGRES_ADMIN_URL")
    if not admin_url:
        pytest.skip("Set TEST_POSTGRES_ADMIN_URL for disposable PostgreSQL test")

    database_name = f"ragdesk_auth_test_{uuid4().hex}"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    database_url = (
        make_url(admin_url)
        .set(database=database_name)
        .render_as_string(hide_password=False)
    )
    engine = create_engine(database_url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
    try:
        monkeypatch.setenv("DATABASE_URL", database_url)
        monkeypatch.setenv("MODEL_PROVIDER", "test")
        monkeypatch.setenv("MODEL_NAME", "unused")
        monkeypatch.setenv("JWT_SECRET", _SECRET)

        command.upgrade(config, "0001_core_schema")
        legacy_id = uuid4()
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO users (id, display_name) VALUES (%s, %s)",
                (legacy_id, "Legacy User"),
            )
        command.upgrade(config, "head")
        with Session(engine) as session:
            legacy = session.get(User, legacy_id)
            assert legacy is not None
            assert legacy.login_name is None
            assert legacy.password_hash is None

        yield engine, config

        command.downgrade(config, "0001_core_schema")
        assert "password_hash" not in {
            column["name"] for column in inspect(engine).get_columns("users")
        }
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin_engine.dispose()


def test_login_and_me_reject_invalid_tokens(auth_database, monkeypatch):
    engine, _config = auth_database
    passwords = iter([_PASSWORD, _PASSWORD])
    monkeypatch.setattr("app.init_demo_users.getpass", lambda _prompt: next(passwords))
    assert init_demo_user(["--login-name", "alice", "--display-name", "Alice"]) == 0

    with Session(engine) as session:
        user = session.scalar(select(User).where(User.login_name == "alice"))
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert _PASSWORD not in user.password_hash
        user_id = user.id

    with TestClient(create_app()) as client:
        login = client.post(
            "/auth/session", json={"login_name": "alice", "password": _PASSWORD}
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        assert login.json()["token_type"] == "bearer"
        assert login.json()["expires_in"] == 1800
        assert login.json()["request_id"] == login.headers["X-Request-ID"]

        me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["user_id"] == str(user_id)
        assert me.json()["login_name"] == "alice"
        assert "password_hash" not in me.json()

        wrong_password = client.post(
            "/auth/session", json={"login_name": "alice", "password": "wrong"}
        )
        unknown_user = client.post(
            "/auth/session", json={"login_name": "unknown", "password": "wrong"}
        )
        assert wrong_password.status_code == unknown_user.status_code == 401
        assert wrong_password.json()["error"] == unknown_user.json()["error"]
        assert wrong_password.headers["WWW-Authenticate"] == "Bearer"

        forged = jwt.encode(
            {"sub": str(user_id), "iss": "ragdesk", "iat": 1, "exp": 4_000_000_000},
            "different-signing-secret-at-least-32-bytes",
            algorithm="HS256",
        )
        expired = create_access_token(
            user_id,
            _SECRET,
            30,
            issued_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        unsigned = jwt.encode(
            {"sub": str(user_id), "iss": "ragdesk", "iat": 1, "exp": 4_000_000_000},
            key="",
            algorithm="none",
        )
        for bad_token in (forged, expired, unsigned):
            response = client.get(
                "/auth/me", headers={"Authorization": f"Bearer {bad_token}"}
            )
            assert response.status_code == 401
            assert response.headers["WWW-Authenticate"] == "Bearer"
        missing = client.get("/auth/me")
        assert missing.status_code == 401
        assert missing.headers["WWW-Authenticate"] == "Bearer"
