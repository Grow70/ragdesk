"""Observe existing services without changing retrieval, tools or model budgets."""

from copy import deepcopy
from dataclasses import asdict
from threading import Lock
from time import perf_counter
from uuid import uuid4

from app.agent.contracts import RunContext
from app.agent.decision import OpenAILoopDecisionClient
from app.agent.loop import run_agent
from app.evaluation.agent_metrics import resources
from app.llm.openai import OpenAIChatClient, OpenAIEmbeddingClient
from app.services.answers import ContextBudget, answer_question
from app.services.traces import RequestTrace, error_code


class ResponseCapture:
    """Private evaluation artifact, not operational trace. Snapshot is thread safe."""

    def __init__(self):
        self.lock, self.entries = Lock(), []

    def record(self, path, payload, attempts):
        with self.lock:
            self.entries.append(
                {"path": path, "payload": deepcopy(payload), "attempts": attempts}
            )

    def snapshot(self):
        with self.lock:
            return deepcopy(self.entries)


class CapturedResponse:
    def __init__(self, *, capture, **kwargs):
        self.capture = capture
        super().__init__(**kwargs)

    def _post(self, path, body):
        payload, attempts, elapsed = super()._post(path, body)
        self.capture.record(path, payload, attempts)
        return payload, attempts, elapsed


class CapturedEmbedding(CapturedResponse, OpenAIEmbeddingClient):
    pass


class CapturedChat(CapturedResponse, OpenAIChatClient):
    pass


class CapturedDecision(CapturedResponse, OpenAILoopDecisionClient):
    pass


def run_arm(
    arm,
    question,
    *,
    factory,
    user_id,
    kb_id,
    profile,
    embedding_factory,
    chat_factory,
    decision_factory,
    index_check,
    index_sha256,
    prices,
    capture=None,
):
    result = {"execution": "not_run", "result": None, "total_ms": None}
    # Preflight is outside measured service latency; check again even after errors.
    if index_check() != index_sha256:
        raise ValueError("INDEX_CHANGED")
    request_id = uuid4().hex
    trace = RequestTrace(request_id, kb_id, prices)
    trace.user_id, trace.preparation_ms = user_id, 0.0
    raw = {}
    start = perf_counter()
    try:
        if arm == "fixed":
            answer = answer_question(
                factory,
                user_id,
                kb_id,
                question,
                5,
                profile,
                trace.wrap_factory("embedding", embedding_factory),
                trace.wrap_factory("chat", chat_factory),
                request_id,
                ContextBudget(),
                trace=raw,
                request_trace=trace,
            )
            result.update(
                result=asdict(answer),
                tool_call_count=0,
                termination_reason="fixed_pipeline",
            )
        elif arm == "agent":
            state = run_agent(
                question,
                context=RunContext(user_id, kb_id, request_id),
                factory=factory,
                profile=profile,
                embedding_factory=embedding_factory,
                decision_factory=decision_factory,
                chat_factory=chat_factory,
            )
            result.update(
                result=asdict(state["final_result"]) if state["final_result"] else None,
                error=state["error"],
                termination_reason=state["termination_reason"],
                tool_call_count=state["tool_call_count"],
                model_call_count=state["model_call_count"],
                history=state["history"],
                trace=state["trace_payload"],
                evidence=[item.model_dump(mode="json") for item in state["evidence"]],
                final_decision=state["decision"],
            )
        else:
            raise ValueError("INVALID_ARM")
        result["execution"] = "complete" if result["result"] is not None else "error"
    except Exception as exc:
        result.update(execution="error", error={"code": error_code(exc)})
    result["total_ms"] = (perf_counter() - start) * 1000
    if arm == "fixed":
        result["raw"] = raw
        result["trace"] = trace.finish(
            200 if result["execution"] == "complete" else 502
        )
        counts = [slot["call_count"] for slot in trace.models.values()]
        result["model_call_count"] = sum(counts) if None not in counts else None
        result["tool_call_count"] = 0
    result["resources"] = resources(result.get("trace", {}), prices)
    result["raw_model_responses"] = capture.snapshot() if capture else []
    result["request_id"] = request_id
    result["backend_citation_validation"] = (
        "passed_source_only"
        if result["execution"] == "complete"
        and result["result"]["status"] == "answered"
        else "not_applicable"
    )
    result["retrieval_call_count"] = (
        1
        if arm == "fixed"
        else sum(h["tool"] == "search_knowledge" for h in result.get("history", []))
    )
    try:
        if index_check() != index_sha256:
            raise ValueError("INDEX_CHANGED")
    except Exception:
        result.update(execution="invalidated", error={"code": "INDEX_OR_AUTH_CHANGED"})
    return result
