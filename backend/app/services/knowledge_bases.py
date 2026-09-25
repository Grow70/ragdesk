"""Knowledge-base authorization and member management."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import KBMember, KnowledgeBase, User
from app.repositories import knowledge_bases as repo


class KnowledgeBaseError(Exception):
    """Base type for expected knowledge-base failures."""


class NotFound(KnowledgeBaseError):
    """The resource is absent or invisible to the caller."""


class AdminRequired(KnowledgeBaseError):
    """A member attempted an administrator operation."""


class LastAdmin(KnowledgeBaseError):
    """The change would leave the knowledge base without an administrator."""


def require_kb_member(session: Session, user_id: UUID, kb_id: UUID) -> KBMember:
    """Check current database membership on every call, without caching JWT roles."""
    member = repo.membership(session, kb_id, user_id)
    if member is None:
        raise NotFound()
    return member


def require_kb_admin(session: Session, user_id: UUID, kb_id: UUID) -> KBMember:
    member = require_kb_member(session, user_id, kb_id)
    if member.role != "admin":
        raise AdminRequired()
    return member


def create_base(session: Session, creator_id: UUID, name: str) -> KnowledgeBase:
    kb = KnowledgeBase(name=name)
    session.add(kb)
    session.flush()
    session.add(KBMember(user_id=creator_id, kb_id=kb.id, role="admin"))
    session.commit()
    session.refresh(kb)
    return kb


def visible_bases(session: Session, user_id: UUID):
    return repo.visible_bases(session, user_id)


def visible_base(session: Session, user_id: UUID, kb_id: UUID):
    member = require_kb_member(session, user_id, kb_id)
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise NotFound()
    return kb, member.role


def members(session: Session, actor_id: UUID, kb_id: UUID):
    require_kb_admin(session, actor_id, kb_id)
    return repo.members_with_users(session, kb_id)


def _lock_for_admin_change(session: Session, actor_id: UUID, kb_id: UUID) -> None:
    require_kb_admin(session, actor_id, kb_id)
    if repo.locked_base(session, kb_id) is None:
        raise NotFound()
    # Membership may have changed while waiting for the row lock.
    session.expire_all()
    require_kb_admin(session, actor_id, kb_id)


def put_member(
    session: Session, actor_id: UUID, kb_id: UUID, target_id: UUID, role: str
) -> KBMember:
    _lock_for_admin_change(session, actor_id, kb_id)
    if session.get(User, target_id) is None:
        raise NotFound()
    member = repo.membership(session, kb_id, target_id)
    if member is None:
        member = KBMember(user_id=target_id, kb_id=kb_id, role=role)
        session.add(member)
    elif member.role == "admin" and role != "admin":
        if repo.admin_count(session, kb_id) <= 1:
            raise LastAdmin()
        member.role = role
    else:
        member.role = role
    session.commit()
    session.refresh(member)
    return member


def remove_member(
    session: Session, actor_id: UUID, kb_id: UUID, target_id: UUID
) -> None:
    _lock_for_admin_change(session, actor_id, kb_id)
    member = repo.membership(session, kb_id, target_id)
    if member is None:
        raise NotFound()
    if member.role == "admin" and repo.admin_count(session, kb_id) <= 1:
        raise LastAdmin()
    session.delete(member)
    session.commit()
