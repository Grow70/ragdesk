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
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_session
from app.http import error_response
from app.models import Document, User
from app.services import documents as service
from app.services import ingestion_jobs as jobs

router = APIRouter(prefix="/knowledge-bases/{kb_id}/documents", tags=["documents"])


class UploadResponse(BaseModel):
    document_id: UUID
    status: Literal["uploaded"]
    job_id: UUID
    job_status: Literal["queued", "running", "succeeded", "failed"]
    status_url: str
    request_id: str


class JobResponse(BaseModel):
    job_id: UUID
    document_id: UUID
    build_id: UUID | None
    status: Literal["queued", "running", "succeeded", "failed"]
    attempts: int
    error_code: str | None
    error_summary: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    request_id: str


class DocumentResponse(BaseModel):
    document_id: UUID
    file_name: str
    file_sha256: str
    status: Literal["uploaded", "queued", "processing", "ready", "failed"]
    created_at: datetime
    request_id: str


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    limit: int
    offset: int
    request_id: str


def _document_response(
    session: Session, document: Document, request_id: str
) -> DocumentResponse:
    return DocumentResponse(
        document_id=document.id,
        file_name=document.file_name,
        file_sha256=document.file_sha256,
        status=jobs.document_status(session, document),
        created_at=document.created_at,
        request_id=request_id,
    )


def install_document_error_handler(app: FastAPI) -> None:
    @app.exception_handler(service.DocumentError)
    async def document_error(request: Request, exc: service.DocumentError):
        return error_response(request, exc.status, exc.code, exc.message)


@router.post("", status_code=202, response_model=UploadResponse)
def upload_document(
    kb_id: UUID,
    request: Request,
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> UploadResponse:
    document_id, job = service.upload(
        session,
        user.id,
        kb_id,
        file,
        request.app.state.settings.upload_storage_dir,
        jobs.configured_profile(request.app.state.settings),
    )
    return _accepted(kb_id, document_id, job, request)


def _accepted(kb_id, document_id, job, request):
    return UploadResponse(
        document_id=document_id,
        status="uploaded",
        job_id=job.id,
        job_status=job.status,
        status_url=f"/knowledge-bases/{kb_id}/documents/{document_id}/jobs/{job.id}",
        request_id=request.state.request_id,
    )


@router.post(
    "/{document_id}/ingestions", status_code=202, response_model=UploadResponse
)
def request_ingestion(
    kb_id: UUID,
    document_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
):
    job = jobs.enqueue(
        session,
        user.id,
        kb_id,
        document_id,
        jobs.configured_profile(request.app.state.settings),
    )
    session.commit()
    return _accepted(kb_id, document_id, job, request)


@router.get("/{document_id}/jobs/{job_id}", response_model=JobResponse)
def get_job(
    kb_id: UUID,
    document_id: UUID,
    job_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
):
    job = jobs.visible_job(session, user.id, kb_id, document_id, job_id)
    return JobResponse(
        job_id=job.id,
        document_id=job.document_id,
        build_id=job.build_id,
        status=job.status,
        attempts=job.attempts,
        error_code=job.error_code,
        error_summary=job.error_summary,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        request_id=request.state.request_id,
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
        items=[_document_response(session, doc, request_id) for doc in items],
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
    return _document_response(session, document, request.state.request_id)


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
