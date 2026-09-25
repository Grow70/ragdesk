"""Knowledge-base and membership HTTP endpoints."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_session
from app.http import error_response
from app.models import User
from app.services import knowledge_bases as service

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


class CreateBaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)


class BaseResponse(BaseModel):
    id: UUID
    name: str
    role: Literal["admin", "member"]
    created_at: datetime
    request_id: str


class BaseListResponse(BaseModel):
    items: list[BaseResponse]
    request_id: str


class PutMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["admin", "member"]


class MemberResponse(BaseModel):
    user_id: UUID
    login_name: str | None
    display_name: str
    role: Literal["admin", "member"]
    request_id: str


class MemberListResponse(BaseModel):
    items: list[MemberResponse]
    request_id: str


class RemoveMemberResponse(BaseModel):
    removed: bool
    request_id: str


def _base_response(kb, role: str, request_id: str) -> BaseResponse:
    return BaseResponse(
        id=kb.id,
        name=kb.name,
        role=role,
        created_at=kb.created_at,
        request_id=request_id,
    )


def install_kb_error_handler(app: FastAPI) -> None:
    @app.exception_handler(service.KnowledgeBaseError)
    async def kb_error(request: Request, exc: service.KnowledgeBaseError):
        if isinstance(exc, service.AdminRequired):
            return error_response(request, 403, "FORBIDDEN", "Administrator required")
        if isinstance(exc, service.LastAdmin):
            return error_response(
                request, 409, "LAST_ADMIN", "At least one administrator is required"
            )
        return error_response(request, 404, "NOT_FOUND", "Not found")


@router.post("", status_code=201, response_model=BaseResponse)
def create_base(
    payload: CreateBaseRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> BaseResponse:
    kb = service.create_base(session, user.id, payload.name)
    return _base_response(kb, "admin", request.state.request_id)


@router.get("", response_model=BaseListResponse)
def list_bases(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> BaseListResponse:
    request_id = request.state.request_id
    return BaseListResponse(
        items=[
            _base_response(kb, role, request_id)
            for kb, role in service.visible_bases(session, user.id)
        ],
        request_id=request_id,
    )


@router.get("/{kb_id}", response_model=BaseResponse)
def get_base(
    kb_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> BaseResponse:
    kb, role = service.visible_base(session, user.id, kb_id)
    return _base_response(kb, role, request.state.request_id)


@router.get("/{kb_id}/members", response_model=MemberListResponse)
def list_members(
    kb_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MemberListResponse:
    request_id = request.state.request_id
    return MemberListResponse(
        items=[
            MemberResponse(
                user_id=member.user_id,
                login_name=member_user.login_name,
                display_name=member_user.display_name,
                role=member.role,
                request_id=request_id,
            )
            for member, member_user in service.members(session, user.id, kb_id)
        ],
        request_id=request_id,
    )


@router.put("/{kb_id}/members/{user_id}", response_model=MemberResponse)
def put_member(
    kb_id: UUID,
    user_id: UUID,
    payload: PutMemberRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MemberResponse:
    member = service.put_member(session, user.id, kb_id, user_id, payload.role)
    target = session.get(User, user_id)
    return MemberResponse(
        user_id=member.user_id,
        login_name=target.login_name,
        display_name=target.display_name,
        role=member.role,
        request_id=request.state.request_id,
    )


@router.delete("/{kb_id}/members/{user_id}", response_model=RemoveMemberResponse)
def remove_member(
    kb_id: UUID,
    user_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> RemoveMemberResponse:
    service.remove_member(session, user.id, kb_id, user_id)
    return RemoveMemberResponse(removed=True, request_id=request.state.request_id)
