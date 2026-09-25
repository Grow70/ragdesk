"""Integration checks create and drop their own random PostgreSQL database."""

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.main import create_app
from app.models import Chunk, Document, DocumentBuild, KBMember, KnowledgeBase, User
from app.repositories.chunks import searchable_chunks_stmt

CORE_TABLES = {
    "users",
    "knowledge_bases",
    "kb_members",
    "documents",
    "document_builds",
    "chunks",
}


def test_core_migration_constraints_query_and_rollback(monkeypatch):
    admin_url = os.getenv("TEST_POSTGRES_ADMIN_URL")
    if not admin_url:
        pytest.skip("Set TEST_POSTGRES_ADMIN_URL for disposable PostgreSQL test")

    database_name = f"ragdesk_test_{uuid4().hex}"
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
        monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-of-at-least-32-bytes-long")

        with TestClient(create_app()) as client:
            assert client.get("/health/live").status_code == 200
        assert inspect(engine).get_table_names() == []

        command.upgrade(config, "head")
        assert CORE_TABLES <= set(inspect(engine).get_table_names())
        assert "embedding" not in {
            column["name"] for column in inspect(engine).get_columns("chunks")
        }
        command.check(config)

        user_id, kb_id, other_kb_id = uuid4(), uuid4(), uuid4()
        document_id, other_document_id = uuid4(), uuid4()
        active_build_id, inactive_build_id, other_build_id = uuid4(), uuid4(), uuid4()

        with Session(engine) as session:
            session.add_all(
                [
                    User(id=user_id, display_name="Demo User"),
                    KnowledgeBase(id=kb_id, name="A"),
                    KnowledgeBase(id=other_kb_id, name="B"),
                ]
            )
            session.commit()
            session.add(KBMember(user_id=user_id, kb_id=kb_id, role="admin"))
            session.add_all(
                [
                    Document(
                        id=document_id,
                        kb_id=kb_id,
                        file_name="a.txt",
                        file_sha256="a" * 64,
                        storage_key="private/a.txt",
                    ),
                    Document(
                        id=other_document_id,
                        kb_id=other_kb_id,
                        file_name="b.txt",
                        file_sha256="b" * 64,
                        storage_key="private/b.txt",
                    ),
                ]
            )
            session.commit()

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    KBMember.__table__.insert().values(
                        id=uuid4(), user_id=user_id, kb_id=kb_id, role="member"
                    )
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    KBMember.__table__.insert().values(
                        id=uuid4(), user_id=uuid4(), kb_id=kb_id, role="member"
                    )
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    Document.__table__.insert().values(
                        id=uuid4(),
                        kb_id=kb_id,
                        file_name="same-content.txt",
                        file_sha256="a" * 64,
                        storage_key="private/duplicate.txt",
                    )
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    Chunk.__table__.insert().values(
                        id=uuid4(),
                        build_id=uuid4(),
                        ordinal=0,
                        body="invalid parent",
                        content_sha256="c" * 64,
                        page_number=1,
                    )
                )

        with Session(engine) as session:
            session.add_all(
                [
                    DocumentBuild(
                        id=active_build_id,
                        document_id=document_id,
                        status="ready",
                        parser_config={},
                        chunking_config={},
                        model_config_id="test-only",
                    ),
                    DocumentBuild(
                        id=inactive_build_id,
                        document_id=document_id,
                        status="ready",
                        parser_config={},
                        chunking_config={},
                        model_config_id="test-only",
                    ),
                    DocumentBuild(
                        id=other_build_id,
                        document_id=other_document_id,
                        status="ready",
                        parser_config={},
                        chunking_config={},
                        model_config_id="test-only",
                    ),
                ]
            )
            session.commit()
            session.add_all(
                [
                    Chunk(
                        build_id=active_build_id,
                        ordinal=0,
                        body="active",
                        content_sha256="d" * 64,
                        page_number=1,
                    ),
                    Chunk(
                        build_id=inactive_build_id,
                        ordinal=0,
                        body="inactive",
                        content_sha256="e" * 64,
                        page_number=1,
                    ),
                    Chunk(
                        build_id=other_build_id,
                        ordinal=0,
                        body="other kb",
                        content_sha256="f" * 64,
                        page_number=1,
                    ),
                ]
            )
            session.commit()

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    update(Document)
                    .where(Document.id == document_id)
                    .values(active_build_id=other_build_id)
                )

        with engine.begin() as connection:
            connection.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(active_build_id=active_build_id)
            )
            connection.execute(
                update(Document)
                .where(Document.id == other_document_id)
                .values(active_build_id=other_build_id)
            )
        with Session(engine) as session:
            assert [
                item.body for item in session.scalars(searchable_chunks_stmt(kb_id))
            ] == ["active"]
            assert [
                item.body
                for item in session.scalars(searchable_chunks_stmt(other_kb_id))
            ] == ["other kb"]

        with engine.begin() as connection:
            connection.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(deleted_at=datetime.now(timezone.utc), active_build_id=None)
            )
        with Session(engine) as session:
            assert list(session.scalars(searchable_chunks_stmt(kb_id))) == []

        engine.dispose()
        command.downgrade(config, "base")
        assert not CORE_TABLES.intersection(inspect(engine).get_table_names())
        command.upgrade(config, "head")
        assert CORE_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin_engine.dispose()
