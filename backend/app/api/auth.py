"""Session creation and verified current-user identity."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_session, unauthorized
from app.models import User
from app.services.auth import authenticate_user, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


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


@router.post("/session", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> LoginResponse:
    user = authenticate_user(session, payload.login_name, payload.password)
    if user is None:
        raise unauthorized()
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
    user: Annotated[User, Depends(get_current_user)],
) -> CurrentUserResponse:
    return CurrentUserResponse(
        user_id=user.id,
        login_name=user.login_name,
        display_name=user.display_name,
        request_id=request.state.request_id,
    )
