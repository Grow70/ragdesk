"""Core PostgreSQL schema; schema changes belong in Alembic migrations."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("login_name", name="uq_users_login_name"),
        CheckConstraint(
            "password_hash IS NULL OR password_hash LIKE '$argon2id$%'",
            name="ck_users_password_hash_argon2id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    login_name: Mapped[str | None] = mapped_column(String(100))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KBMember(Base):
    __tablename__ = "kb_members"
    __table_args__ = (
        UniqueConstraint("user_id", "kb_id", name="uq_kb_members_user_kb"),
        CheckConstraint("role IN ('admin', 'member')", name="ck_kb_members_role"),
        Index("ix_kb_members_kb_id", "kb_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    kb_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("knowledge_bases.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("length(file_sha256) = 64", name="ck_documents_hash_length"),
        ForeignKeyConstraint(
            ["id", "active_build_id"],
            ["document_builds.document_id", "document_builds.id"],
            name="fk_documents_active_build_same_document",
            use_alter=True,
        ),
        Index("ix_documents_kb_deleted", "kb_id", "deleted_at"),
        Index("ix_documents_active_build_id", "active_build_id"),
        Index(
            "uq_documents_live_hash",
            "kb_id",
            "file_sha256",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    kb_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("knowledge_bases.id"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_build_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DocumentBuild(Base):
    __tablename__ = "document_builds"
    __table_args__ = (
        UniqueConstraint("document_id", "id", name="uq_document_builds_document_id"),
        CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'failed')",
            name="ck_document_builds_status",
        ),
        CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL",
            name="ck_document_builds_failed_error",
        ),
        Index("ix_document_builds_document_created", "document_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    parser_config: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    chunking_config: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    model_config_id: Mapped[str] = mapped_column(String(200), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("build_id", "ordinal", name="uq_chunks_build_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal"),
        CheckConstraint("length(content_sha256) = 64", name="ck_chunks_hash_length"),
        CheckConstraint(
            "page_number IS NULL OR page_number > 0", name="ck_chunks_page_number"
        ),
        CheckConstraint(
            "start_line IS NULL OR start_line > 0", name="ck_chunks_start_line"
        ),
        CheckConstraint(
            "end_line IS NULL OR (start_line IS NOT NULL AND end_line >= start_line)",
            name="ck_chunks_end_line",
        ),
        CheckConstraint(
            "page_number IS NOT NULL OR start_line IS NOT NULL "
            "OR (heading_path IS NOT NULL AND jsonb_array_length(heading_path) > 0)",
            name="ck_chunks_locator",
        ),
        CheckConstraint(
            "heading_path IS NULL OR jsonb_typeof(heading_path) = 'array'",
            name="ck_chunks_heading_path",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    build_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document_builds.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    heading_path: Mapped[list[str] | None] = mapped_column(JSONB)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
