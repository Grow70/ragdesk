"""Fixed-flow RAG answers and authorized citation source views."""

from dataclasses import asdict, dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_current_user, get_session
from app.api.retrieval import retrieval_runtime
from app.http import error_response
from app.llm.openai import OpenAIChatClient
from app.models import User
from app.repositories.sources import Source
from app.services import answers as service

router = APIRouter(prefix="/knowledge-bases/{kb_id}", tags=["answers"])


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)


@dataclass(frozen=True, slots=True)
class SourceResponse(Source):
    request_id: str


def install_answer_error_handler(app: FastAPI):
    @app.exception_handler(service.AnswerError)
    async def answer_error(request: Request, exc: service.AnswerError):
        return error_response(request, exc.status, exc.code, exc.message)


@router.post("/answers", response_model=service.AnswerResult)
def answer(
    kb_id: UUID,
    payload: AnswerRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
):
    user_id = user.id
    session.close()
    profile, embedding_factory = retrieval_runtime(request)
    settings = request.app.state.settings
    budget = getattr(request.app.state, "answer_budget", service.ContextBudget())

    def default_chat():
        if settings.openai_api_key is None:
            raise service.AnswerError(
                "MODEL_NOT_CONFIGURED", 503, "Chat service is not configured"
            )
        return OpenAIChatClient(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.chat_model,
            connect_timeout=settings.model_connect_timeout_seconds,
            read_timeout=settings.model_read_timeout_seconds,
            max_attempts=settings.model_max_attempts,
            max_completion_tokens=budget.output_tokens,
        )

    return service.answer_question(
        sessionmaker(request.app.state.engine),
        user_id,
        kb_id,
        payload.question,
        payload.top_k,
        profile,
        embedding_factory,
        getattr(request.app.state, "answer_chat_factory", default_chat),
        request.state.request_id,
        budget,
    )


@router.get(
    "/sources/{document_id}/{build_id}/{chunk_id}", response_model=SourceResponse
)
def source(
    kb_id: UUID,
    document_id: UUID,
    build_id: UUID,
    chunk_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
):
    user_id = user.id
    session.close()
    value = service.get_source(
        sessionmaker(request.app.state.engine),
        user_id,
        kb_id,
        document_id,
        build_id,
        chunk_id,
    )
    return SourceResponse(**asdict(value), request_id=request.state.request_id)
