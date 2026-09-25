"""Knowledge-base scoped queries; authorization decisions live in services."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import KBMember, KnowledgeBase, User


def membership(session: Session, kb_id: UUID, user_id: UUID) -> KBMember | None:
    return session.scalar(
        select(KBMember).where(KBMember.kb_id == kb_id, KBMember.user_id == user_id)
    )


def visible_bases(session: Session, user_id: UUID):
    return session.execute(
        select(KnowledgeBase, KBMember.role)
        .join(KBMember, KBMember.kb_id == KnowledgeBase.id)
        .where(KBMember.user_id == user_id)
        .order_by(KnowledgeBase.created_at, KnowledgeBase.id)
    ).all()


def locked_base(session: Session, kb_id: UUID) -> KnowledgeBase | None:
    return session.scalar(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id).with_for_update()
    )


def admin_count(session: Session, kb_id: UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(KBMember)
            .where(KBMember.kb_id == kb_id, KBMember.role == "admin")
        )
        or 0
    )


def members_with_users(session: Session, kb_id: UUID):
    return session.execute(
        select(KBMember, User)
        .join(User, User.id == KBMember.user_id)
        .where(KBMember.kb_id == kb_id)
        .order_by(KBMember.created_at, KBMember.id)
    ).all()
