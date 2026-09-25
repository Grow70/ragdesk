"""Queries scoped to one knowledge base and nondeleted documents."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Document


def by_hash(session: Session, kb_id: UUID, sha256: str) -> Document | None:
    return session.scalar(
        select(Document).where(
            Document.kb_id == kb_id,
            Document.file_sha256 == sha256,
            Document.deleted_at.is_(None),
        )
    )


def by_id(session: Session, kb_id: UUID, document_id: UUID) -> Document | None:
    return session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.kb_id == kb_id,
            Document.deleted_at.is_(None),
        )
    )


def page(session: Session, kb_id: UUID, limit: int, offset: int):
    scope = (Document.kb_id == kb_id, Document.deleted_at.is_(None))
    total = session.scalar(select(func.count()).select_from(Document).where(*scope))
    items = session.scalars(
        select(Document)
        .where(*scope)
        .order_by(Document.created_at, Document.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return items, total
