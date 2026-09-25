"""Shared request dependencies for database sessions and verified identities."""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.models import User
from app.services.auth import verified_user_id

_bearer = HTTPBearer(auto_error=False)


def get_session(request: Request):
    with Session(request.app.state.engine) as session:
        yield session


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise unauthorized()
    try:
        user_id = verified_user_id(
            credentials.credentials,
            request.app.state.settings.jwt_secret.get_secret_value(),
        )
    except jwt.InvalidTokenError as exc:
        raise unauthorized() from exc
    user = session.get(User, user_id)
    if user is None or user.login_name is None or user.password_hash is None:
        raise unauthorized()
    return user
