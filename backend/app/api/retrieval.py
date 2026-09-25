"""Authenticated vector-search debug endpoint."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_current_user, get_session
from app.http import error_response
from app.llm.fake import FakeEmbeddingClient
from app.llm.openai import OpenAIEmbeddingClient
from app.models import User
from app.services import retrieval as service
from app.services.ingest import EmbeddingProfile

router = APIRouter(prefix="/knowledge-bases/{kb_id}", tags=["retrieval"])


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)


class SearchItem(BaseModel):
    chunk_id: UUID
    document_id: UUID
    build_id: UUID
    knowledge_base_id: UUID
    document_name: str
    text: str
    page_number: int | None
    heading_path: list[str] | None
    distance: float
    rank: int


class SearchResponse(BaseModel):
    distance_metric: str
    items: list[SearchItem]
    request_id: str


def install_retrieval_error_handler(app: FastAPI) -> None:
    @app.exception_handler(service.RetrievalError)
    async def retrieval_error(request: Request, exc: service.RetrievalError):
        return error_response(request, exc.status, exc.code, exc.message)


@router.post("/search", response_model=SearchResponse)
def search(
    kb_id: UUID,
    payload: SearchRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> SearchResponse:
    user_id = user.id
    session.close()  # Release authentication's transaction before embedding.
    settings = request.app.state.settings
    if settings.retrieval_embedding_backend == "fake":
        default_profile = EmbeddingProfile.fake(
            dimensions=settings.embedding_dimensions
        )
    else:
        default_profile = EmbeddingProfile(
            provider="openai",
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
    profile = getattr(request.app.state, "retrieval_profile", default_profile)

    def default_client():
        if settings.retrieval_embedding_backend == "fake":
            return FakeEmbeddingClient(dimensions=settings.embedding_dimensions)
        if settings.openai_api_key is None:
            raise service.RetrievalError(
                "MODEL_NOT_CONFIGURED", 503, "Embedding service is not configured"
            )
        return OpenAIEmbeddingClient(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            connect_timeout=settings.model_connect_timeout_seconds,
            read_timeout=settings.model_read_timeout_seconds,
            max_attempts=settings.model_max_attempts,
        )

    factory = getattr(request.app.state, "retrieval_client_factory", default_client)
    items = service.search(
        sessionmaker(request.app.state.engine),
        user_id,
        kb_id,
        payload.query,
        payload.top_k,
        profile,
        factory,
    )
    return SearchResponse(
        distance_metric="cosine_distance",
        items=[SearchItem.model_validate(item, from_attributes=True) for item in items],
        request_id=request.state.request_id,
    )
