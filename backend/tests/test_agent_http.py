"""HTTP adapter uses the real Agent loop with controlled models and PostgreSQL."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_agent_graph import ANSWER, FACT
from test_agent_loop import Embedding, search
from test_answers import _answer_app
from test_retrieval import _headers, _seed
from test_retrieval import retrieval_db as retrieval_db

from app.agent.decision import FakeDecisionClient
from app.llm.contracts import ModelError
from app.llm.fake import FakeChatClient
from app.models import KBMember


def agent_app(chat=None, decisions=None):
    app = _answer_app(chat or FakeChatClient(ANSWER))
    app.state.retrieval_client_factory = Embedding
    outputs = decisions or [search("审批期限"), {"tool_calls": []}]

    class Decision(FakeDecisionClient):
        def decide(self, messages):
            history = json.loads(messages[1]["content"])["history"]
            self.content = outputs[min(len(history), len(outputs) - 1)]
            return super().decide(messages)

    app.state.agent_decision_factory = lambda: Decision({})
    return app


def ask(client, ids, **fields):
    return client.post(
        f"/knowledge-bases/{ids['a']}/answers",
        headers=_headers(ids["alice"]),
        json={"question": "审批期限？", "mode": "agent", **fields},
    )


def test_agent_http_result_events_trace_and_fresh_round(retrieval_db):
    engine, ids = retrieval_db
    _seed(engine, ids["a"], FACT, [1.0] + [0.0] * 1535)
    with TestClient(agent_app()) as client:
        first = ask(client, ids)
        assert first.status_code == 200, first.text
        data = first.json()
        assert data["status"] == "answered" and data["mode"] == "agent"
        assert data["tool_call_count"] == 1 and data["model_call_count"] == 4
        assert data["events"] == [
            {
                "step": 1,
                "tool": "search_knowledge",
                "status": "success",
                "query_summary": "审批期限",
                "result_count": 1,
            }
        ]
        assert (
            "evidence" not in data
            and "history" not in data
            and "trace_payload" not in data
        )
        source = client.get(
            data["citations"][0]["source_path"], headers=_headers(ids["alice"])
        )
        assert source.status_code == 200 and FACT in source.json()["snippet"]
        trace = client.get(
            f"/knowledge-bases/{ids['a']}/traces/{data['request_id']}",
            headers=_headers(ids["alice"]),
        ).json()
        assert trace["models"]["chat"]["call_count"] == 3
        assert trace["models"]["embedding"]["call_count"] == 1
        assert FACT not in json.dumps(trace, ensure_ascii=False)
        again = ask(client, ids).json()
        assert (
            again["request_id"] != data["request_id"] and again["tool_call_count"] == 1
        )
        assert (
            client.post(
                f"/knowledge-bases/{ids['b']}/answers",
                headers=_headers(ids["alice"]),
                json={"question": "x", "mode": "agent"},
            ).status_code
            == 404
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": "invalid"},
        {"user_id": "forged"},
        {"history": []},
        {"tool_call_count": 9},
        {"top_k": 20},
    ],
)
def test_agent_rejects_mutable_context_and_unknown_mode(retrieval_db, fields):
    _, ids = retrieval_db
    with TestClient(agent_app()) as client:
        assert ask(client, ids, **fields).status_code == 422


@pytest.mark.parametrize(
    "content",
    [
        {"status": "answered", "answer": "假答案 [c99]", "citation_ids": ["c99"]},
        "bad JSON",
    ],
)
def test_agent_invalid_output_is_error_not_success(retrieval_db, content):
    engine, ids = retrieval_db
    _seed(engine, ids["a"], FACT, [1.0] + [0.0] * 1535)
    with TestClient(agent_app(FakeChatClient(content))) as client:
        result = ask(client, ids)
        assert result.status_code == 502
        assert "answer" not in result.json() and "error" in result.json()


def test_agent_timeout_and_revocation_are_not_insufficient(retrieval_db):
    engine, ids = retrieval_db
    _seed(engine, ids["a"], FACT, [1.0] + [0.0] * 1535)

    class TimeoutChat(FakeChatClient):
        def generate(self, *args):
            raise ModelError("MODEL_TIMEOUT", attempts=1)

    with TestClient(agent_app(TimeoutChat(ANSWER))) as client:
        result = ask(client, ids)
        assert (
            result.status_code == 504
            and result.json()["events"][0]["status"] == "success"
        )

    class RevokeChat(FakeChatClient):
        def generate(self, *args):
            with Session(engine) as session:
                row = (
                    session.query(KBMember)
                    .filter_by(user_id=ids["alice"], kb_id=ids["a"])
                    .one()
                )
                session.delete(row)
                session.commit()
            return super().generate(*args)

    with TestClient(agent_app(RevokeChat(ANSWER))) as client:
        result = ask(client, ids)
        assert result.status_code == 404
        assert "answer" not in result.json() and not result.json().get("events")


def test_agent_empty_budget_and_missing_real_config(retrieval_db, monkeypatch):
    _, ids = retrieval_db
    app = agent_app(decisions=[search("一"), search("二"), search("三")])
    with TestClient(app) as client:
        data = ask(client, ids).json()
        assert (
            data["status"] == "insufficient_evidence"
            and data["termination_reason"] == "tool_budget"
        )
        assert len(data["events"]) == 3
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = _answer_app(FakeChatClient(ANSWER))
    with TestClient(app) as client:
        response = ask(client, ids)
        assert (
            response.status_code == 503
            and response.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"
        )


def test_deadline_snapshot_is_partial_and_counts_are_not_fabricated(retrieval_db):
    from threading import Event

    from app.llm.budget import RoundLimits

    _, ids = retrieval_db
    release, finished = Event(), Event()

    class SlowDecision(FakeDecisionClient):
        def decide(self, messages):
            try:
                release.wait(2)
                return super().decide(messages)
            finally:
                finished.set()

    app = agent_app()
    app.state.agent_limits = RoundLimits(seconds=0.2)
    app.state.agent_decision_factory = lambda: SlowDecision({"tool_calls": []})
    try:
        with TestClient(app) as client:
            response = ask(client, ids)
            assert response.status_code == 504
            data = response.json()
            assert data["termination_reason"] == "deadline"
            trace = client.get(
                f"/knowledge-bases/{ids['a']}/traces/{data['request_id']}",
                headers=_headers(ids["alice"]),
            ).json()
            assert trace["events"][-1]["trace_incomplete"]
            assert trace["models"]["chat"]["call_count"] is None
            assert trace["models"]["chat"]["usage"] is None
            assert trace["cost"]["estimated_total"] is None
    finally:
        release.set()
        assert finished.wait(2)
