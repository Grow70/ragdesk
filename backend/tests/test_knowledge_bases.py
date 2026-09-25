"""Isolated knowledge-base authorization and member lifecycle checks."""

import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic import command
from app.main import create_app
from app.models import User
from app.services.auth import create_access_token, hash_password
from app.services.knowledge_bases import (
    AdminRequired,
    NotFound,
    require_kb_admin,
    require_kb_member,
)

_SECRET = "kb-test-secret-with-at-least-32-bytes"


@pytest.fixture
def kb_database(monkeypatch):
    admin_url = os.getenv("TEST_POSTGRES_ADMIN_URL")
    if not admin_url:
        pytest.skip("Set TEST_POSTGRES_ADMIN_URL for disposable PostgreSQL test")
    database_name = f"ragdesk_kb_test_{uuid4().hex}"
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
        command.upgrade(config, "head")
        user_ids = {name: uuid4() for name in ("alice", "bob", "carol")}
        with Session(engine) as session:
            session.add_all(
                User(
                    id=user_id,
                    login_name=name,
                    display_name=name.title(),
                    password_hash=hash_password(f"password-for-{name}"),
                )
                for name, user_id in user_ids.items()
            )
            session.commit()
        yield engine, user_ids
    finally:
        engine.dispose()
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin_engine.dispose()


def _headers(user_id: UUID) -> dict[str, str]:
    token = create_access_token(user_id, _SECRET, 30)
    return {"Authorization": f"Bearer {token}"}


def test_two_bases_isolate_users_and_removal_takes_effect(kb_database):
    engine, user_ids = kb_database
    alice, bob, carol = (user_ids[name] for name in ("alice", "bob", "carol"))
    alice_headers, bob_headers, carol_headers = map(_headers, (alice, bob, carol))

    with TestClient(create_app()) as client:
        assert client.get("/knowledge-bases").status_code == 401
        a_response = client.post(
            "/knowledge-bases", json={"name": "A"}, headers=alice_headers
        )
        b_response = client.post(
            "/knowledge-bases", json={"name": "B"}, headers=bob_headers
        )
        assert a_response.status_code == b_response.status_code == 201
        a_id, b_id = a_response.json()["id"], b_response.json()["id"]
        assert a_response.json()["role"] == "admin"
        assert [
            x["id"]
            for x in client.get("/knowledge-bases", headers=alice_headers).json()[
                "items"
            ]
        ] == [a_id]
        assert [
            x["id"]
            for x in client.get("/knowledge-bases", headers=bob_headers).json()["items"]
        ] == [b_id]
        assert (
            client.get("/knowledge-bases", headers=carol_headers).json()["items"] == []
        )

        forbidden_b = client.get(f"/knowledge-bases/{b_id}", headers=alice_headers)
        absent = client.get(f"/knowledge-bases/{uuid4()}", headers=alice_headers)
        assert forbidden_b.status_code == absent.status_code == 404
        assert forbidden_b.json()["error"] == absent.json()["error"]
        forbidden_change = client.put(
            f"/knowledge-bases/{b_id}/members/{carol}",
            json={"role": "admin"},
            headers=alice_headers,
        )
        absent_change = client.put(
            f"/knowledge-bases/{uuid4()}/members/{carol}",
            json={"role": "admin"},
            headers=alice_headers,
        )
        assert forbidden_change.status_code == absent_change.status_code == 404
        assert forbidden_change.json()["error"] == absent_change.json()["error"]
        assert (
            client.get(f"/knowledge-bases/{a_id}", headers=carol_headers).status_code
            == 404
        )
        assert (
            client.post(
                "/knowledge-bases",
                json={"name": "Spoofed", "user_id": str(bob)},
                headers=alice_headers,
            ).status_code
            == 422
        )
        assert (
            client.put(
                f"/knowledge-bases/{a_id}/members/{uuid4()}",
                json={"role": "member"},
                headers=alice_headers,
            ).status_code
            == 404
        )

        add_bob = client.put(
            f"/knowledge-bases/{a_id}/members/{bob}",
            json={"role": "member"},
            headers=alice_headers,
        )
        assert add_bob.status_code == 200
        assert (
            client.get(f"/knowledge-bases/{a_id}", headers=bob_headers).status_code
            == 200
        )
        assert {
            x["id"]
            for x in client.get("/knowledge-bases", headers=bob_headers).json()["items"]
        } == {a_id, b_id}

        # The same guard must be called by future upload and delete handlers.
        with Session(engine) as session:
            assert require_kb_member(session, bob, UUID(a_id)).role == "member"
            with pytest.raises(AdminRequired):
                require_kb_admin(session, bob, UUID(a_id))
            with pytest.raises(NotFound):
                require_kb_member(session, carol, UUID(a_id))
        assert (
            client.get(
                f"/knowledge-bases/{a_id}/members", headers=bob_headers
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"/knowledge-bases/{a_id}/members/{carol}",
                json={"role": "admin"},
                headers=bob_headers,
            ).status_code
            == 403
        )
        assert (
            client.delete(
                f"/knowledge-bases/{a_id}/members/{alice}", headers=bob_headers
            ).status_code
            == 403
        )

        assert (
            client.delete(
                f"/knowledge-bases/{a_id}/members/{bob}", headers=alice_headers
            ).status_code
            == 200
        )
        assert (
            client.get(f"/knowledge-bases/{a_id}", headers=bob_headers).status_code
            == 404
        )
        assert (
            client.get(f"/knowledge-bases/{b_id}", headers=bob_headers).status_code
            == 200
        )
        assert [
            x["id"]
            for x in client.get("/knowledge-bases", headers=bob_headers).json()["items"]
        ] == [b_id]


def test_last_admin_cannot_be_removed_or_demoted(kb_database):
    _engine, user_ids = kb_database
    alice, carol = user_ids["alice"], user_ids["carol"]
    alice_headers, carol_headers = _headers(alice), _headers(carol)

    with TestClient(create_app()) as client:
        a_id = client.post(
            "/knowledge-bases", json={"name": "A"}, headers=alice_headers
        ).json()["id"]
        path = f"/knowledge-bases/{a_id}/members"
        only_admin_remove = client.delete(f"{path}/{alice}", headers=alice_headers)
        only_admin_demote = client.put(
            f"{path}/{alice}", json={"role": "member"}, headers=alice_headers
        )
        assert only_admin_remove.status_code == only_admin_demote.status_code == 409

        assert (
            client.put(
                f"{path}/{carol}", json={"role": "admin"}, headers=alice_headers
            ).status_code
            == 200
        )
        assert (
            client.delete(f"{path}/{alice}", headers=carol_headers).status_code == 200
        )
        assert (
            client.get(f"/knowledge-bases/{a_id}", headers=alice_headers).status_code
            == 404
        )
        assert (
            client.delete(f"{path}/{carol}", headers=carol_headers).status_code == 409
        )
        assert (
            client.put(
                f"{path}/{carol}", json={"role": "member"}, headers=carol_headers
            ).status_code
            == 409
        )
        members = client.get(path, headers=carol_headers)
        assert members.status_code == 200
        assert [(x["user_id"], x["role"]) for x in members.json()["items"]] == [
            (str(carol), "admin")
        ]
