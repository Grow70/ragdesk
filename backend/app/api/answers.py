"""Fixed-flow RAG answers and authorized citation source views."""

from dataclasses import asdict, dataclass
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session, sessionmaker

from app.agent.contracts import RunContext
from app.agent.decision import OpenAILoopDecisionClient
from app.api.dependencies import get_current_user, get_session
from app.api.retrieval import retrieval_runtime
from app.http import error_response
from app.llm.budget import RoundLimits
from app.llm.openai import OpenAIChatClient
from app.models import User
from app.repositories.sources import Source
from app.services import agent_answers, traces
from app.services import answers as service

router = APIRouter(prefix="/knowledge-bases/{kb_id}", tags=["answers"])


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)
    mode: Literal["rag", "agent"] = "rag"

    @model_validator(mode="after")
    def agent_top_k(self):
        if self.mode == "agent" and self.top_k != 5:
            raise ValueError("Agent chooses its own bounded top_k")
        return self


@dataclass(frozen=True, slots=True)
class SourceResponse(Source):
    request_id: str


def install_answer_error_handler(app: FastAPI):
    @app.exception_handler(service.AnswerError)
    async def answer_error(request: Request, exc: service.AnswerError):
        if isinstance(exc, agent_answers.AgentError):
            request.state.trace_error = exc.code
            return JSONResponse(
                status_code=exc.status,
                content={
                    "error": {"code": exc.code, "message": exc.message},
                    "request_id": request.state.request_id,
                    **exc.details,
                },
            )
        return error_response(request, exc.status, exc.code, exc.message)


def traced_user(
    request: Request, user: Annotated[User, Depends(get_current_user)]
) -> User:
    request.state.answer_trace.user_id = user.id
    return user


@router.post("/answers", response_model=agent_answers.AnswerResponse)
def answer(
    kb_id: UUID,
    payload: AnswerRequest,
    request: Request,
    user: Annotated[User, Depends(traced_user)],
    session: Annotated[Session, Depends(get_session)],
):
    user_id = user.id
    trace = request.state.answer_trace
    trace.preparation_ms = (trace.clock() - trace.start) * 1000
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

    chat_factory = getattr(request.app.state, "answer_chat_factory", default_chat)
    if payload.mode == "agent":

        def default_decision():
            if settings.openai_api_key is None:
                raise service.AnswerError(
                    "MODEL_NOT_CONFIGURED", 503, "Decision model is not configured"
                )
            return OpenAILoopDecisionClient(
                api_key=settings.openai_api_key.get_secret_value(),
                model=settings.chat_model,
                connect_timeout=settings.model_connect_timeout_seconds,
                read_timeout=settings.model_read_timeout_seconds,
                max_attempts=settings.model_max_attempts,
                max_completion_tokens=budget.output_tokens,
            )

        return agent_answers.answer_with_agent(
            payload.question,
            context=RunContext(user_id, kb_id, request.state.request_id),
            factory=sessionmaker(request.app.state.engine),
            profile=profile,
            embedding_factory=embedding_factory,
            decision_factory=getattr(
                request.app.state, "agent_decision_factory", default_decision
            ),
            chat_factory=chat_factory,
            trace=trace,
            limits=getattr(request.app.state, "agent_limits", RoundLimits()),
        )
    result = service.answer_question(
        sessionmaker(request.app.state.engine),
        user_id,
        kb_id,
        payload.question,
        payload.top_k,
        profile,
        trace.wrap_factory("embedding", embedding_factory),
        trace.wrap_factory(
            "chat", getattr(request.app.state, "answer_chat_factory", default_chat)
        ),
        request.state.request_id,
        budget,
        request_trace=trace,
    )
    return agent_answers.AnswerResponse(
        result.status, result.answer, result.citations, result.request_id
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


@router.get("/traces/{trace_id}")
def get_trace(
    kb_id: UUID,
    trace_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
):
    return traces.read(session, user.id, kb_id, trace_id.hex)
