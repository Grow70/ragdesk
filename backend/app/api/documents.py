"""Authorized upload, metadata, and private original-file endpoints."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_session
from app.http import error_response
from app.models import Document, User
from app.services import documents as service

router = APIRouter(prefix="/knowledge-bases/{kb_id}/documents", tags=["documents"])


class UploadResponse(BaseModel):
    document_id: UUID
    status: Literal["uploaded"]
    request_id: str


class DocumentResponse(BaseModel):
    document_id: UUID
    file_name: str
    file_sha256: str
    status: Literal["uploaded"]
    created_at: datetime
    request_id: str


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    limit: int
    offset: int
    request_id: str


def _document_response(document: Document, request_id: str) -> DocumentResponse:
    return DocumentResponse(
        document_id=document.id,
        file_name=document.file_name,
        file_sha256=document.file_sha256,
        status="uploaded",
        created_at=document.created_at,
        request_id=request_id,
    )


def install_document_error_handler(app: FastAPI) -> None:
    @app.exception_handler(service.DocumentError)
    async def document_error(request: Request, exc: service.DocumentError):
        return error_response(request, exc.status, exc.code, exc.message)


@router.post("", status_code=201, response_model=UploadResponse)
def upload_document(
    kb_id: UUID,
    request: Request,
    response: Response,
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> UploadResponse:
    document_id, created = service.upload(
        session, user.id, kb_id, file, request.app.state.settings.upload_storage_dir
    )
    if not created:
        response.status_code = 200
    return UploadResponse(
        document_id=document_id, status="uploaded", request_id=request.state.request_id
    )


@router.get("", response_model=DocumentListResponse)
def list_documents(
    kb_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    items, total = service.list_documents(session, user.id, kb_id, limit, offset)
    request_id = request.state.request_id
    return DocumentListResponse(
        items=[_document_response(doc, request_id) for doc in items],
        total=total,
        limit=limit,
        offset=offset,
        request_id=request_id,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    kb_id: UUID,
    document_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> DocumentResponse:
    document = service.get_document(session, user.id, kb_id, document_id)
    return _document_response(document, request.state.request_id)


@router.get("/{document_id}/raw", response_class=FileResponse)
def get_original(
    kb_id: UUID,
    document_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> FileResponse:
    document = service.get_document(session, user.id, kb_id, document_id)
    path = service.original_path(
        document, request.app.state.settings.upload_storage_dir
    )
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=document.file_name,
        headers={"X-Content-Type-Options": "nosniff"},
    )
