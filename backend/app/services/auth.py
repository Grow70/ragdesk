"""Password verification and signed access tokens."""

from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User

_HASHER = PasswordHasher()
_DUMMY_HASH = _HASHER.hash(token_urlsafe(32))
_JWT_ALGORITHM = "HS256"
_JWT_ISSUER = "ragdesk"


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def authenticate_user(session: Session, login_name: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.login_name == login_name))
    candidate_hash = user.password_hash if user and user.password_hash else _DUMMY_HASH
    try:
        valid = _HASHER.verify(candidate_hash, password)
    except VerifyMismatchError:
        valid = False
    return user if valid and user and user.password_hash else None


def create_access_token(
    user_id: UUID,
    secret: str,
    ttl_minutes: int,
    *,
    issued_at: datetime | None = None,
) -> str:
    now = issued_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("issued_at must include a timezone")
    return jwt.encode(
        {
            "sub": str(user_id),
            "iss": _JWT_ISSUER,
            "iat": now,
            "exp": now + timedelta(minutes=ttl_minutes),
        },
        secret,
        algorithm=_JWT_ALGORITHM,
    )


def verified_user_id(token: str, secret: str) -> UUID:
    payload = jwt.decode(
        token,
        secret,
        algorithms=[_JWT_ALGORITHM],
        issuer=_JWT_ISSUER,
        options={"require": ["sub", "iss", "iat", "exp"]},
    )
    subject = payload["sub"]
    if not isinstance(subject, str):
        raise jwt.InvalidTokenError("Invalid subject")
    try:
        return UUID(subject)
    except ValueError as exc:
        raise jwt.InvalidTokenError("Invalid subject") from exc
