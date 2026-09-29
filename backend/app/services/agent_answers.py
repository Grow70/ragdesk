"""HTTP-facing projection of the existing bounded Agent; no new orchestration."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Literal

from app.agent.contracts import RunContext
from app.agent.loop import run_agent
from app.llm.budget import RoundLimits
from app.services.answers import AnswerError, AnswerResult, get_source
from app.services.knowledge_bases import require_kb_member
from app.services.traces import estimate


@dataclass(frozen=True, slots=True)
class ToolEvent:
    step: int
    tool: Literal["search_knowledge", "read_chunks"]
    status: str
    query_summary: str | None
    result_count: int | None


@dataclass(frozen=True, slots=True)
class AnswerResponse(AnswerResult):
    mode: Literal["rag", "agent"] = "rag"
    events: list[ToolEvent] = field(default_factory=list)
    termination_reason: str | None = None
    tool_call_count: int | None = None
    model_call_count: int | None = None


class AgentError(AnswerError):
    def __init__(self, code, status, details):
        super().__init__(code, status, "Agent request failed")
        self.details = details


def _events(state):
    return [
        ToolEvent(
            step=item["step"],
            tool=item["tool"],
            status=item["status"],
            query_summary=item["query"][:160] if item.get("query") else None,
            result_count=item["result_count"],
        )
        for item in state["history"][:3]
        if item["tool"] in {"search_knowledge", "read_chunks"}
    ]


def _attach_trace(trace, state, started):
    """Copy a completed/supervisor snapshot, never share a live worker object."""
    payload = deepcopy(state["trace_payload"])
    for attr in (
        "models",
        "events",
        "candidates",
        "evidence",
        "citations",
        "degradations",
        "outcome",
    ):
        if attr in payload:
            setattr(trace, attr, payload[attr])
    if payload.get("trace_incomplete"):
        for slot in trace.models.values():
            slot.update(
                status="unknown",
                usage_status="unknown",
                call_count=None,
                method_calls=None,
            )
    for event in trace.events:
        event["offset_ms"] += (started - trace.start) * 1000
    # The inner loop has no HTTP price settings. Estimate only from known usage.
    for slot in trace.models.values():
        for call in slot["calls"]:
            call["cost"] = estimate(
                trace.prices,
                call["provider"],
                call["model"],
                call["usage"],
                call["usage_status"] == "reported",
                trace.created_at.date(),
            )
    trace.events.append(
        {
            "event_id": f"e{len(trace.events) + 1}",
            "parent_event_id": None,
            "kind": "stage",
            "name": "agent_round",
            "offset_ms": (started - trace.start) * 1000,
            "duration_ms": (trace.clock() - started) * 1000,
            "status": "error" if state["error"] else "complete",
            "error_type": state["error"]["code"] if state["error"] else None,
            "termination_reason": state["termination_reason"],
            "trace_incomplete": payload.get("trace_incomplete", False),
            "tool_call_count": state["tool_call_count"],
            "model_call_count": state["model_call_count"],
        }
    )


def answer_with_agent(
    question,
    *,
    context: RunContext,
    factory,
    profile,
    embedding_factory,
    decision_factory,
    chat_factory,
    trace,
    limits=RoundLimits(),
):
    with factory() as session:
        require_kb_member(session, context.user_id, context.kb_id)
    started = trace.clock()
    state = run_agent(
        question,
        context=context,
        factory=factory,
        profile=profile,
        embedding_factory=embedding_factory,
        decision_factory=decision_factory,
        chat_factory=chat_factory,
        limits=limits,
    )
    _attach_trace(trace, state, started)
    # No prior tool summaries are released if permission changed during this round.
    with factory() as session:
        require_kb_member(session, context.user_id, context.kb_id)
    error = state["error"]
    events = _events(state)
    if error:
        if error["status"] in {403, 404, 409, 410}:
            events = []
        raise AgentError(
            error["code"],
            error["status"],
            {
                "mode": "agent",
                "events": [asdict(e) for e in events],
                "termination_reason": state["termination_reason"],
                "tool_call_count": state["tool_call_count"],
                "model_call_count": state["model_call_count"],
            },
        )
    answer = state["final_result"]
    if answer is None:
        raise AnswerError("MODEL_INVALID_RESPONSE", 502, "Missing validated answer")
    for citation in answer.citations:
        source = get_source(
            factory,
            context.user_id,
            context.kb_id,
            citation.document_id,
            citation.build_id,
            citation.chunk_id,
        )
        if not source.snippet.startswith(citation.snippet):
            raise AnswerError("EVIDENCE_CHANGED", 409, "Evidence changed")
    return AnswerResponse(
        status=answer.status,
        answer=answer.answer,
        citations=answer.citations,
        request_id=answer.request_id,
        mode="agent",
        events=events,
        termination_reason=state["termination_reason"],
        tool_call_count=state["tool_call_count"],
        model_call_count=state["model_call_count"],
    )
