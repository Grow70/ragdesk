"""Upload and private document access against an isolated PostgreSQL database."""

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic import command
from app.main import create_app
from app.models import Document, User
from app.services.auth import create_access_token, hash_password

_SECRET = "documents-test-secret-with-at-least-32-bytes"


@pytest.fixture
def document_env(monkeypatch, tmp_path):
    admin_url = os.getenv("TEST_POSTGRES_ADMIN_URL")
    if not admin_url:
        pytest.skip("Set TEST_POSTGRES_ADMIN_URL for disposable PostgreSQL test")
    database_name = f"ragdesk_docs_test_{uuid4().hex}"
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
        storage = tmp_path / "private-uploads"
        monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(storage))
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
        yield engine, storage, user_ids
    finally:
        engine.dispose()
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin_engine.dispose()


def _headers(user_id):
    return {"Authorization": f"Bearer {create_access_token(user_id, _SECRET, 30)}"}


def _upload(client, kb_id, headers, name, data):
    return client.post(
        f"/knowledge-bases/{kb_id}/documents",
        files={"file": (name, data, "application/octet-stream")},
        headers=headers,
    )


def _pdf() -> bytes:
    chunks = [b"%PDF-1.4\n"]
    offsets = []
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n",
    ]
    for item in objects:
        offsets.append(sum(map(len, chunks)))
        chunks.append(item)
    xref_offset = sum(map(len, chunks))
    chunks.append(b"xref\n0 3\n0000000000 65535 f \n")
    chunks.extend(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    chunks.append(
        (
            f"trailer\n<< /Root 1 0 R /Size 3 >>\nstartxref\n{xref_offset}\n"
            + "%%EOF\n"
        ).encode()
    )
    return b"".join(chunks)


def test_upload_formats_dedup_listing_and_protected_raw(document_env):
    engine, storage, users = document_env
    alice, bob, carol = (_headers(users[name]) for name in ("alice", "bob", "carol"))
    with TestClient(create_app()) as client:
        kb_a = client.post(
            "/knowledge-bases", json={"name": "A"}, headers=alice
        ).json()["id"]
        kb_b = client.post(
            "/knowledge-bases", json={"name": "B"}, headers=alice
        ).json()["id"]
        assert (
            client.put(
                f"/knowledge-bases/{kb_a}/members/{users['bob']}",
                json={"role": "member"},
                headers=alice,
            ).status_code
            == 200
        )
        files = [
            ("policy.md", "# 演示数据\n规则。\n".encode()),
            ("notes.txt", "演示数据：备注。\n".encode()),
            ("manual.pdf", _pdf()),
        ]
        ids = []
        for name, data in files:
            uploaded = _upload(client, kb_a, alice, name, data)
            assert uploaded.status_code == 202, uploaded.text
            assert uploaded.json()["status"] == "uploaded"
            document_id = uploaded.json()["document_id"]
            ids.append(document_id)
            detail = client.get(
                f"/knowledge-bases/{kb_a}/documents/{document_id}", headers=bob
            )
            assert detail.status_code == 200
            assert detail.json()["file_sha256"] == hashlib.sha256(data).hexdigest()
            assert detail.json()["file_name"] == name
            assert "storage_key" not in detail.json()
            original = client.get(
                f"/knowledge-bases/{kb_a}/documents/{document_id}/raw", headers=bob
            )
            assert original.status_code == 200
            assert original.content == data
            assert original.headers["x-content-type-options"] == "nosniff"
        page = client.get(
            f"/knowledge-bases/{kb_a}/documents?limit=2&offset=1", headers=bob
        )
        assert page.status_code == 200
        assert page.json()["total"] == 3
        assert page.json()["limit"] == 2
        assert page.json()["offset"] == 1
        assert len(page.json()["items"]) == 2
        assert all(item["status"] == "queued" for item in page.json()["items"])
        duplicate = _upload(client, kb_a, alice, "renamed.md", files[0][1])
        assert duplicate.status_code == 202
        assert duplicate.json()["document_id"] == ids[0]
        assert (
            client.get(f"/knowledge-bases/{kb_a}/documents", headers=bob).json()[
                "total"
            ]
            == 3
        )
        other_kb = _upload(client, kb_b, alice, files[0][0], files[0][1])
        assert other_kb.status_code == 202
        assert other_kb.json()["document_id"] != ids[0]
        with Session(engine) as session:
            docs = session.scalars(select(Document)).all()
            assert len(docs) == 4
            assert all(doc.active_build_id is None for doc in docs)
            assert all("policy.md" not in doc.storage_key for doc in docs)
            assert all((storage / doc.storage_key).is_file() for doc in docs)
        assert not list((storage / "staging").iterdir())
        for path in (
            f"/knowledge-bases/{kb_a}/documents",
            f"/knowledge-bases/{kb_a}/documents/{ids[0]}",
            f"/knowledge-bases/{kb_a}/documents/{ids[0]}/raw",
        ):
            assert client.get(path, headers=carol).status_code == 404
            assert client.get(path, headers=bob).status_code == 200
        assert client.get("/objects/" + uuid4().hex).status_code == 404
        assert (
            client.get(
                f"/knowledge-bases/{kb_b}/documents/{ids[0]}", headers=alice
            ).status_code
            == 404
        )
        assert (
            client.delete(
                f"/knowledge-bases/{kb_a}/members/{users['bob']}", headers=alice
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/knowledge-bases/{kb_a}/documents/{ids[0]}/raw", headers=bob
            ).status_code
            == 404
        )


def test_upload_rejects_invalid_input_and_unauthorized_users(document_env):
    engine, storage, users = document_env
    alice, bob, carol = (_headers(users[name]) for name in ("alice", "bob", "carol"))
    with TestClient(create_app()) as client:
        kb_id = client.post(
            "/knowledge-bases", json={"name": "A"}, headers=alice
        ).json()["id"]
        client.put(
            f"/knowledge-bases/{kb_id}/members/{users['bob']}",
            json={"role": "member"},
            headers=alice,
        )
        for name, data, status in (
            ("empty.txt", b"", 400),
            ("large.txt", b"a" * (10 * 1024 * 1024 + 1), 413),
            ("fake.pdf", b"plain text", 400),
            ("fake.txt", _pdf(), 400),
            ("binary.md", b"\x00\x01hello", 400),
            ("image.png", b"\x89PNG", 400),
            ("../../evil.txt", b"text", 400),
            ("..\\evil.txt", b"text", 400),
        ):
            result = _upload(client, kb_id, alice, name, data)
            assert result.status_code == status, (name, result.text)
            assert "request_id" in result.json()
        assert _upload(client, kb_id, bob, "valid.txt", b"hello").status_code == 403
        assert _upload(client, kb_id, carol, "valid.txt", b"hello").status_code == 404
        assert _upload(client, uuid4(), alice, "valid.txt", b"hello").status_code == 404
        assert _upload(client, kb_id, {}, "valid.txt", b"hello").status_code == 401
        boundary = _upload(
            client, kb_id, alice, "boundary.txt", b"a" * (10 * 1024 * 1024)
        )
        assert boundary.status_code == 202
        with Session(engine) as session:
            assert len(session.scalars(select(Document)).all()) == 1
        assert len(list((storage / "objects").iterdir())) == 1
        assert not list((storage / "staging").iterdir())


def test_database_failure_removes_new_file(document_env, monkeypatch):
    engine, storage, users = document_env
    alice = _headers(users["alice"])
    with TestClient(create_app()) as client:
        kb_id = client.post(
            "/knowledge-bases", json={"name": "A"}, headers=alice
        ).json()["id"]

        def fail_document_commit(session):
            raise RuntimeError("simulated database failure")

        with monkeypatch.context() as patch:
            patch.setattr(Session, "commit", fail_document_commit)
            result = _upload(client, kb_id, alice, "valid.txt", b"hello")
        assert result.status_code == 500
        assert result.json()["error"]["code"] == "INTERNAL_ERROR"
        with Session(engine) as session:
            assert session.scalars(select(Document)).all() == []
        assert not list((storage / "staging").iterdir())
        assert not list((storage / "objects").iterdir())
