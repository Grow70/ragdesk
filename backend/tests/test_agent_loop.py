"""Bounded adaptive graph with fixed model scripts and real authorization."""

import asyncio
import json
from datetime import datetime, timezone
from threading import Event
from time import monotonic
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker
from test_agent_graph import ANSWER, FACT, call, response, wire_call
from test_agent_tools import VECTOR, _member_remove
from test_retrieval import PROFILE, _seed
from test_retrieval import retrieval_db as retrieval_db

from app.agent.contracts import RunContext
from app.agent.decision import FakeDecisionClient, OpenAILoopDecisionClient
from app.agent.loop import run_agent
from app.llm.budget import BudgetExceeded, RequestBudget, RoundLimits
from app.llm.contracts import EmbeddingResult, ModelError, ModelUsage
from app.llm.fake import FakeChatClient, FakeEmbeddingClient
from app.llm.openai import OpenAIChatClient, OpenAIEmbeddingClient
from app.models import Chunk, Document, DocumentBuild


class ScriptedDecision(FakeDecisionClient):
    def __init__(self, outputs):
        super().__init__({})
        self.outputs, self.inputs = list(outputs), []

    def decide(self, messages):
        self.inputs.append(json.loads(messages[1]["content"]))
        self.content = self.outputs.pop(0)
        return super().decide(messages)


class Embedding(FakeEmbeddingClient):
    def __init__(self, *, fail_first=False):
        super().__init__()
        self.fail_first, self.queries = fail_first, []

    def embed_query(self, text):
        self.call_count += 1
        self.queries.append(text)
        if self.fail_first and self.call_count == 1:
            raise ModelError("MODEL_TIMEOUT", attempts=1)
        return EmbeddingResult([VECTOR], ModelUsage(), 0.0, 1)


def search(query, top_k=1):
    return call(arguments={"query": query, "top_k": top_k})


def run(env, decisions, *, embedding=None, chat=None, **kwargs):
    engine, ids = env
    decision = (
        decisions
        if isinstance(decisions, FakeDecisionClient)
        else ScriptedDecision(decisions)
    )
    embedding, chat = embedding or Embedding(), chat or FakeChatClient(ANSWER)
    result = run_agent(
        "审批期限？",
        context=RunContext(ids["alice"], ids["a"], uuid4().hex),
        factory=sessionmaker(engine),
        profile=PROFILE,
        embedding_factory=lambda: embedding,
        decision_factory=lambda: decision,
        chat_factory=lambda: chat,
        **kwargs,
    )
    return result, decision, embedding, chat


def test_failed_retrieval_result_drives_rewrite_then_success(retrieval_db):
    engine, ids = retrieval_db
    chunk_id = _seed(engine, ids["a"], FACT, VECTOR)
    state, decision, embedding, chat = run(
        retrieval_db,
        [search("报销规则"), search("审批期限 680 CNY"), {"tool_calls": []}],
        embedding=Embedding(fail_first=True),
    )
    assert state["error"] is None and state["final_result"].status == "answered"
    assert state["model_call_count"] == 6 and state["tool_call_count"] == 2
    assert embedding.queries == ["报销规则", "审批期限 680 CNY"]
    assert decision.inputs[1]["latest_tool_status"] == "technical_failure"
    assert decision.inputs[1]["latest_tool_error"]["code"] == "MODEL_TIMEOUT"
    assert decision.inputs[2]["untrusted_evidence"][0]["chunk_id"] == str(chunk_id)
    assert state["history"][0]["status"] == "technical_failure"
    assert state["history"][1]["added"] == [str(chunk_id)]
    trace = json.dumps(state["trace_payload"], ensure_ascii=False)
    assert "审批期限 680 CNY" in trace and FACT not in trace
    assert not state["trace_payload"]["trace_incomplete"] and chat.call_count == 1
    assert state["context_chars"] == sum(h["output_chars"] for h in state["history"])
    print(
        json.dumps(
            {
                "validation": "fake_only",
                "request_id": state["final_result"].request_id,
                "history": state["history"],
                "model_requests": state["model_call_count"],
                "tool_calls": state["tool_call_count"],
                "context_chars": state["context_chars"],
                "final_status": state["final_result"].status,
                "termination_reason": state["termination_reason"],
            },
            ensure_ascii=False,
        )
    )


def test_search_then_read_then_final_answer(retrieval_db):
    engine, ids = retrieval_db
    chunk_id = _seed(engine, ids["a"], "short", VECTOR)
    with Session(engine) as session:
        session.get(Chunk, chunk_id).body = FACT * 30
        session.commit()
    state, _, embedding, _ = run(
        retrieval_db,
        [
            search("审批"),
            call("read_chunks", {"chunk_ids": [str(chunk_id)]}),
            {"tool_calls": []},
        ],
    )
    assert state["error"] is None and state["final_result"].status == "answered"
    assert state["history"][1]["changed"] == [str(chunk_id)]
    assert len(state["final_result"].citations[0].snippet) > 240
    assert embedding.call_count == 1 and state["model_call_count"] == 5


def test_repeat_is_rejected_before_second_tool_execution(retrieval_db):
    engine, ids = retrieval_db
    _seed(engine, ids["a"], FACT, VECTOR)
    state, _, embedding, _ = run(
        retrieval_db, [search("Error  E101"), search(" error e101 ")]
    )
    assert state["termination_reason"] == "repeated_call"
    assert state["tool_call_count"] == embedding.call_count == 1
    assert state["final_result"].status == "answered" and state["model_call_count"] == 4


def test_always_empty_stops_after_three_tools(retrieval_db):
    state, decision, embedding, chat = run(
        retrieval_db, [search("a"), search("b"), search("c")]
    )
    assert state["termination_reason"] == "tool_budget"
    assert state["tool_call_count"] == decision.call_count == 3
    assert (
        state["model_call_count"] == 3 and embedding.call_count == chat.call_count == 0
    )
    assert state["final_result"].status == "insufficient_evidence"


def test_model_budget_reserves_final_request_and_never_starts_a_seventh(retrieval_db):
    engine, ids = retrieval_db
    _seed(engine, ids["a"], FACT, VECTOR)
    state, decision, embedding, chat = run(
        retrieval_db, [search("a"), search("b")], limits=RoundLimits(model_requests=4)
    )
    assert state["termination_reason"] == "model_budget"
    assert state["model_call_count"] <= 4 and state["tool_call_count"] <= 2
    assert state["final_result"].status == "insufficient_evidence"
    assert embedding.call_count == 1 and chat.call_count == 0


def test_context_budget_discards_unadmitted_tool_text(retrieval_db):
    engine, ids = retrieval_db
    _seed(engine, ids["a"], FACT, VECTOR)
    state, _, _, chat = run(
        retrieval_db, [search("q")], limits=RoundLimits(context_chars=1)
    )
    assert state["termination_reason"] == "context_budget"
    assert state["context_chars"] == 0 and not state["evidence"]
    assert (
        state["final_result"].status == "insufficient_evidence" and chat.call_count == 0
    )


def test_model_requests_clarification_without_unverified_rules(retrieval_db):
    state, _, _, chat = run(
        retrieval_db,
        [call("request_clarification", {"missing_fields": ["product", "time_range"]})],
    )
    assert state["termination_reason"] == "clarification"
    assert state["final_result"].status == "needs_clarification"
    assert state["final_result"].answer == "请补充产品或型号、时间范围。"
    assert (
        state["model_call_count"] == 1
        and state["tool_call_count"] == chat.call_count == 0
    )


@pytest.mark.parametrize("phase", ["embedding", "decision", "chat", "delete"])
def test_revocation_or_deletion_during_calls_never_returns_answer(retrieval_db, phase):
    engine, ids = retrieval_db
    chunk_id = _seed(engine, ids["a"], FACT, VECTOR)

    def change():
        if phase != "delete":
            _member_remove(engine, ids["alice"], ids["a"])
        else:
            with Session(engine) as session:
                build = session.get(
                    DocumentBuild, session.get(Chunk, chunk_id).build_id
                )
                session.get(Document, build.document_id).deleted_at = datetime.now(
                    timezone.utc
                )
                session.commit()

    class ChangingEmbedding(Embedding):
        def embed_query(self, text):
            if phase == "embedding":
                change()
            return super().embed_query(text)

    class ChangingDecision(ScriptedDecision):
        def decide(self, messages):
            if phase == "decision" and self.call_count == 1:
                change()
            return super().decide(messages)

    class ChangingChat(FakeChatClient):
        def generate(self, messages, schema):
            if phase in {"chat", "delete"}:
                change()
            return super().generate(messages, schema)

    state, _, _, _ = run(
        retrieval_db,
        ChangingDecision([search("q"), {"tool_calls": []}]),
        embedding=ChangingEmbedding(),
        chat=ChangingChat(ANSWER),
    )
    assert state["error"]["code"] == (
        "EVIDENCE_CHANGED" if phase == "delete" else "ACCESS_DENIED"
    )
    assert state["final_result"] is None and not state["evidence"]


def test_deadline_uses_controlled_clock_and_stops_next_model(retrieval_db):
    now = [0.0]

    class Late(ScriptedDecision):
        def decide(self, messages):
            result = super().decide(messages)
            now[0] = 61.0
            return result

    state, _, embedding, chat = run(
        retrieval_db, Late([search("q")]), clock=lambda: now[0]
    )
    assert state["termination_reason"] == "deadline"
    assert state["final_result"] is None and state["tool_call_count"] == 0
    assert embedding.call_count == chat.call_count == 0


def test_supervisor_returns_without_waiting_for_blocked_fake(retrieval_db):
    entered, release, finished = Event(), Event(), Event()

    class Blocked(ScriptedDecision):
        def decide(self, messages):
            entered.set()
            try:
                release.wait(5)
                return super().decide(messages)
            finally:
                finished.set()

    start = monotonic()
    try:
        state, _, embedding, chat = run(
            retrieval_db, Blocked([search("q")]), limits=RoundLimits(seconds=0.15)
        )
        assert entered.is_set()
        assert monotonic() - start < 1.0
        assert (
            state["termination_reason"] == "deadline" and state["final_result"] is None
        )
        assert state["trace_payload"]["trace_incomplete"] is True
        assert embedding.call_count == chat.call_count == 0
    finally:
        release.set()
        assert finished.wait(2)


@pytest.mark.parametrize(
    "limits",
    [
        {"tool_calls": 4},
        {"model_requests": 7},
        {"seconds": 61},
        {"seconds": float("inf")},
        {"context_chars": 24001},
    ],
)
def test_limits_cannot_raise_hard_caps(limits):
    with pytest.raises(ValueError):
        RoundLimits(**limits)


def test_real_http_retries_share_six_requests_and_timeouts_shrink():
    now, seen, counts = [0.0], [], {}

    def handler(request):
        body = json.loads(request.content)
        kind = (
            "embedding"
            if request.url.path.endswith("embeddings")
            else "decision"
            if "tools" in body
            else "chat"
        )
        counts[kind] = counts.get(kind, 0) + 1
        seen.append((kind, request.extensions["timeout"], 10 - now[0]))
        now[0] += 0.5
        if counts[kind] == 1:
            return httpx.Response(429)
        if kind == "embedding":
            payload = {
                "model": "text-embedding-3-small",
                "data": [{"index": 0, "embedding": VECTOR}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        elif kind == "decision":
            payload = response([wire_call()])
        else:
            payload = response(content=json.dumps(ANSWER))
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    budget = RequestBudget(RoundLimits(seconds=10), clock=lambda: now[0])
    budget.pause = lambda delay: now.__setitem__(0, now[0] + delay)
    decision = OpenAILoopDecisionClient(api_key="offline", budget_transport=transport)
    embedding = OpenAIEmbeddingClient(api_key="offline", budget_transport=transport)
    chat = OpenAIChatClient(api_key="offline", budget_transport=transport)
    from app.services.answers import ANSWER_SCHEMA

    try:
        with budget.scope():
            decision.decide([{"role": "user", "content": "q"}])
            embedding.embed_query("q")
        with budget.scope(final=True):
            chat.generate([{"role": "user", "content": "q"}], ANSWER_SCHEMA)
            with pytest.raises(BudgetExceeded, match="AGENT_MODEL_BUDGET"):
                decision.decide([{"role": "user", "content": "q"}])
    finally:
        decision.close()
        embedding.close()
        chat.close()
    assert len(seen) == budget.requests == 6 and all(c == 2 for c in counts.values())
    assert all(
        0 < value <= remaining
        for _, timeouts, remaining in seen
        for value in timeouts.values()
    )
    assert seen[-1][1]["read"] < seen[0][1]["read"]


def test_budgeted_http_cancels_stalled_response():
    cancelled = Event()

    async def handler(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    budget = RequestBudget(RoundLimits(seconds=0.05))
    client = OpenAILoopDecisionClient(
        api_key="offline", budget_transport=httpx.MockTransport(handler)
    )
    try:
        with (
            budget.scope(),
            pytest.raises(BudgetExceeded, match="AGENT_DEADLINE_TIMEOUT"),
        ):
            client.decide([{"role": "user", "content": "q"}])
    finally:
        client.close()
    assert cancelled.is_set() and budget.requests == 1


def test_native_clarification_uses_control_schema():
    def handler(request):
        body = json.loads(request.content)
        assert len(body["tools"]) == 3 and not body["parallel_tool_calls"]
        return httpx.Response(
            200,
            json=response(
                [wire_call("request_clarification", '{"missing_fields":["product"]}')]
            ),
        )

    client = OpenAILoopDecisionClient(
        api_key="offline", budget_transport=httpx.MockTransport(handler)
    )
    try:
        with RequestBudget().scope():
            result = client.decide([{"role": "user", "content": "q"}])
        assert result.content["tool_calls"][0]["arguments"] == {
            "missing_fields": ["product"]
        }
    finally:
        client.close()


def test_unknown_model_client_is_rejected_before_its_hidden_requests(retrieval_db):
    class Unknown:
        call_count = 0

        def decide(self, messages):
            self.call_count += 1
            raise AssertionError("must never execute an unbudgeted client")

    engine, ids = retrieval_db
    unknown = Unknown()
    state = run_agent(
        "q",
        context=RunContext(ids["alice"], ids["a"], uuid4().hex),
        factory=sessionmaker(engine),
        profile=PROFILE,
        embedding_factory=Embedding,
        decision_factory=lambda: unknown,
        chat_factory=lambda: FakeChatClient(ANSWER),
    )
    assert state["error"]["code"] == "UNBUDGETED_MODEL_CLIENT"
    assert state["model_call_count"] == unknown.call_count == 0


def test_three_distinct_tools_then_final_stays_in_six_request_limit(retrieval_db):
    engine, ids = retrieval_db
    first = _seed(engine, ids["a"], "first fact", VECTOR)
    second = _seed(engine, ids["a"], "second fact", VECTOR)
    state, _, _, chat = run(
        retrieval_db,
        [
            search("q", 2),
            call("read_chunks", {"chunk_ids": [str(first)]}),
            call("read_chunks", {"chunk_ids": [str(second)]}),
        ],
    )
    assert state["termination_reason"] == "tool_budget"
    assert state["tool_call_count"] == 3 and state["model_call_count"] == 5
    assert (
        state["final_result"].citations[0].chunk_id == second and chat.call_count == 1
    )
    assert state["history"][2]["removed"] == [str(first)]


def test_reordered_read_ids_are_still_a_duplicate_call(retrieval_db):
    engine, ids = retrieval_db
    first = _seed(engine, ids["a"], "first", VECTOR)
    second = _seed(engine, ids["a"], "second", VECTOR)
    state, _, _, _ = run(
        retrieval_db,
        [
            search("q", 2),
            call("read_chunks", {"chunk_ids": [str(first), str(second)]}),
            call("read_chunks", {"chunk_ids": [str(second), str(first)]}),
        ],
    )
    assert (
        state["termination_reason"] == "repeated_call" and state["tool_call_count"] == 2
    )


def test_unrecovered_tool_timeout_is_not_mislabeled_no_results(retrieval_db):
    engine, ids = retrieval_db
    _seed(engine, ids["a"], FACT, VECTOR)
    state, _, _, chat = run(
        retrieval_db,
        [search("q"), {"tool_calls": []}],
        embedding=Embedding(fail_first=True),
    )
    assert state["error"]["code"] == "MODEL_TIMEOUT" and state["error"]["status"] == 504
    assert state["final_result"] is None and chat.call_count == 0


def test_supervisor_keeps_inflight_search_query_with_unknown_result(retrieval_db):
    engine, ids = retrieval_db
    _seed(engine, ids["a"], FACT, VECTOR)
    entered, release, finished = Event(), Event(), Event()

    class BlockedEmbedding(Embedding):
        def embed_query(self, text):
            entered.set()
            try:
                release.wait(5)
                return super().embed_query(text)
            finally:
                finished.set()

    try:
        state, _, _, chat = run(
            retrieval_db,
            [search("实际已发出的查询")],
            embedding=BlockedEmbedding(),
            limits=RoundLimits(seconds=0.3),
        )
        assert entered.is_set() and state["termination_reason"] == "deadline"
        assert state["history"][0]["query"] == "实际已发出的查询"
        assert state["history"][0]["status"] == "running"
        assert state["history"][0]["result_count"] is None
        assert state["trace_payload"]["usage"] is None and chat.call_count == 0
    finally:
        release.set()
        assert finished.wait(2)
