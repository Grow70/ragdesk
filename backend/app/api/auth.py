"""Session creation and verified current-user identity."""

from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models import User
from app.services.auth import authenticate_user, create_access_token, verified_user_id

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)
_AUTH_ERROR = "Invalid authentication credentials"


class LoginRequest(BaseModel):
    login_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    request_id: str


class CurrentUserResponse(BaseModel):
    user_id: UUID
    login_name: str
    display_name: str
    request_id: str


def _session(request: Request):
    with Session(request.app.state.engine) as session:
        yield session


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=_AUTH_ERROR,
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/session", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    session: Annotated[Session, Depends(_session)],
) -> LoginResponse:
    user = authenticate_user(session, payload.login_name, payload.password)
    if user is None:
        raise _unauthorized()
    settings = request.app.state.settings
    ttl_minutes = settings.access_token_ttl_minutes
    return LoginResponse(
        access_token=create_access_token(
            user.id, settings.jwt_secret.get_secret_value(), ttl_minutes
        ),
        expires_in=ttl_minutes * 60,
        request_id=request.state.request_id,
    )


@router.get("/me", response_model=CurrentUserResponse)
def current_user(
    request: Request,
    session: Annotated[Session, Depends(_session)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUserResponse:
    if credentials is None:
        raise _unauthorized()
    try:
        user_id = verified_user_id(
            credentials.credentials,
            request.app.state.settings.jwt_secret.get_secret_value(),
        )
    except jwt.InvalidTokenError as exc:
        raise _unauthorized() from exc
    user = session.get(User, user_id)
    if user is None or user.login_name is None or user.password_hash is None:
        raise _unauthorized()
    return CurrentUserResponse(
        user_id=user.id,
        login_name=user.login_name,
        display_name=user.display_name,
        request_id=request.state.request_id,
    )
