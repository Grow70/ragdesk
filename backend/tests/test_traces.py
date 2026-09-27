"""Trace privacy, permissions and honest accounting with offline models."""

import json
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from test_answers import _answer_app, _ask, _indexed
from test_retrieval import QueryFake, _headers
from test_retrieval import retrieval_db as retrieval_db

from alembic import command
from app.llm.contracts import ChatResult, ModelError, ModelUsage
from app.llm.fake import FakeChatClient
from app.models import AnswerTrace, KBMember
from app.retrieval.reranker import FakeReranker, RerankResult, RerankScore
from app.services import traces

OUTPUT = {"status": "answered", "answer": "10 天。[c1]", "citation_ids": ["c1"]}


def _get(client, ids, response, user="alice", kb="a"):
    return client.get(
        f"/knowledge-bases/{ids[kb]}/traces/{response.json()['request_id']}",
        headers=_headers(ids[user]),
    )


def test_trace_roundtrip_is_redacted_and_request_id_is_server_owned(retrieval_db):
    engine, ids = retrieval_db
    sentinel = "private-source-question-answer-sentinel"
    chunk_id = _indexed(engine, ids["a"], sentinel)
    chat = FakeChatClient({**OUTPUT, "answer": sentinel + " [c1]"})
    app = _answer_app(chat)
    with TestClient(app) as client:
        response = client.post(
            f"/knowledge-bases/{ids['a']}/answers",
            json={"question": sentinel},
            headers={**_headers(ids["alice"]), "X-Request-ID": "forged"},
        )
        assert (
            response.status_code == 200
            and response.headers["X-Trace-Status"] == "stored"
        )
        trace = _get(client, ids, response).json()
        assert trace["request_id"] == response.headers["X-Request-ID"] != "forged"
        assert trace["user_id"] == str(ids["alice"]) and trace[
            "knowledge_base_id"
        ] == str(ids["a"])
        assert trace["retrieval_strategy"] == "vector" and not trace["degraded"]
        assert trace["candidates"][0]["chunk_id"] == str(chunk_id)
        assert trace["candidates"][0]["rank"] == 1
        assert trace["citations"][0]["citation_id"] == "c1"
        assert (
            trace["models"]["embedding"]["call_count"]
            == trace["models"]["chat"]["call_count"]
            == 1
        )
        assert trace["models"]["embedding"]["calls"][0]["model"] == QueryFake.model
        assert trace["models"]["chat"]["calls"][0]["provider"] == "fake"
        assert trace["models"]["rerank"]["status"] == "not_run"
        assert trace["models"]["rerank"]["usage"] is None
        assert trace["cost"]["estimated_total"] is None
        assert trace["models"]["chat"]["usage"] is None
        assert trace["total_ms"] >= trace["timings_ms"]["retrieval"] > 0
        by_name = {e["name"]: e for e in trace["events"]}
        assert (
            by_name["model.embedding"]["parent_event_id"]
            == by_name["retrieval"]["event_id"]
        )
        serialized = json.dumps(trace)
        for forbidden in (
            sentinel,
            "Bearer ",
            "untrusted_evidence",
            "vectors",
            "raw_response",
            "password",
        ):
            assert forbidden not in serialized
        if os.getenv("STEP22_TRACE_SAMPLE_OUTPUT"):
            path = Path(os.environ["STEP22_TRACE_SAMPLE_OUTPUT"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    with Session(engine) as session:
        assert session.get(AnswerTrace, trace["request_id"]).payload == trace


def test_trace_permissions_owner_admin_other_member_cross_kb_and_revocation(
    retrieval_db,
):
    engine, ids = retrieval_db
    _indexed(engine, ids["a"])
    with TestClient(_answer_app(FakeChatClient(OUTPUT))) as client:
        response = _ask(client, ids)
        assert _get(client, ids, response, "bob").status_code == 404
        assert _get(client, ids, response, "bob", "b").status_code == 404
        with Session(engine) as session:
            session.add(KBMember(user_id=ids["bob"], kb_id=ids["a"], role="member"))
            session.commit()
        assert _get(client, ids, response, "bob").status_code == 404
        with Session(engine) as session:
            member = session.scalar(
                select(KBMember).where(
                    KBMember.user_id == ids["bob"], KBMember.kb_id == ids["a"]
                )
            )
            member.role = "admin"
            session.commit()
        assert _get(client, ids, response, "bob").status_code == 200
        with Session(engine) as session:
            member = session.scalar(
                select(KBMember).where(
                    KBMember.user_id == ids["alice"], KBMember.kb_id == ids["a"]
                )
            )
            session.delete(member)
            session.commit()
        assert _get(client, ids, response).status_code == 404
        assert (
            client.get(
                f"/knowledge-bases/{ids['a']}/traces/{uuid4()}",
                headers=_headers(ids["bob"]),
            ).status_code
            == 404
        )


@pytest.mark.parametrize("status", ["insufficient_evidence", "needs_clarification"])
def test_refusal_and_clarification_are_distinct(retrieval_db, status):
    engine, ids = retrieval_db
    _indexed(engine, ids["a"])
    chat = FakeChatClient(
        {"status": status, "answer": "资料不足或需要限定条件", "citation_ids": []}
    )
    with TestClient(_answer_app(chat)) as client:
        result = _ask(client, ids)
        trace = _get(client, ids, result).json()
        assert trace["outcome"] == status
        assert trace["refused"] is (status == "insufficient_evidence")
        assert not trace["citations"] and trace["error_type"] is None


def test_empty_and_rejected_requests_still_have_trace(retrieval_db):
    engine, ids = retrieval_db
    with TestClient(_answer_app(FakeChatClient(OUTPUT))) as client:
        result = _ask(client, ids, kb="empty")
        trace = _get(client, ids, result, kb="empty").json()
        assert trace["refused"] and trace["models"]["chat"]["call_count"] == 0
        assert trace["models"]["embedding"]["usage"] is None
        unauthorized = _ask(client, ids, user="bob")
        invalid = client.post(
            f"/knowledge-bases/{ids['a']}/answers",
            json={"question": ""},
            headers=_headers(ids["alice"]),
        )
        missing_token = client.post(
            f"/knowledge-bases/{ids['a']}/answers", json={"question": "q"}
        )
        absent = client.post(
            f"/knowledge-bases/{uuid4()}/answers",
            json={"question": "q"},
            headers=_headers(ids["alice"]),
        )
        assert (
            unauthorized.status_code,
            invalid.status_code,
            missing_token.status_code,
            absent.status_code,
        ) == (404, 422, 401, 404)
    with Session(engine) as session:
        for result in (unauthorized, invalid, missing_token, absent):
            payload = session.get(AnswerTrace, result.json()["request_id"]).payload
            assert payload["outcome"] == "error" and payload["error_type"]
            assert payload["models"]["chat"]["call_count"] == 0
        assert (
            session.get(AnswerTrace, missing_token.json()["request_id"]).user_id is None
        )


@pytest.mark.parametrize(
    "role,code,http_status",
    [
        ("chat", "MODEL_TIMEOUT", 504),
        ("embedding", "MODEL_TIMEOUT", 504),
        ("chat", "MODEL_INVALID_RESPONSE", 502),
    ],
)
def test_model_failures_record_unknown_usage_without_secret(
    retrieval_db, role, code, http_status
):
    engine, ids = retrieval_db
    _indexed(engine, ids["a"])

    class Failing:
        provider, model, dimensions = "fake", QueryFake.model, 1536
        call_count = 0

        def fail(self, *args):
            self.call_count += 2
            raise ModelError(code, attempts=2)

        generate = embed_query = fail

    app = _answer_app(FakeChatClient(OUTPUT))
    if role == "chat":
        app.state.answer_chat_factory = Failing
    else:
        app.state.retrieval_client_factory = Failing
    with TestClient(app) as client:
        result = _ask(client, ids)
        assert result.status_code == http_status
        trace = _get(client, ids, result).json()
        assert trace["error_type"] == code and trace["timeout"] is ("TIMEOUT" in code)
        assert trace["models"][role]["call_count"] == 2
        assert trace["models"][role]["usage"] is None
        assert trace["cost"]["estimated_total"] is None


def test_storage_failure_does_not_mask_answer_and_leaks_no_exception(
    retrieval_db, monkeypatch, caplog
):
    engine, ids = retrieval_db
    _indexed(engine, ids["a"])

    def broken(*args):
        raise RuntimeError("private-db-password-sentinel")

    monkeypatch.setattr(traces, "persist", broken)
    with TestClient(_answer_app(FakeChatClient(OUTPUT))) as client:
        with caplog.at_level("ERROR", logger="ragdesk"):
            result = _ask(client, ids)
        assert (
            result.status_code == 200
            and result.headers["X-Trace-Status"] == "unavailable"
        )
        assert _get(client, ids, result).status_code == 404
    assert "private-db-password-sentinel" not in result.text + caplog.text


def price(**changes):
    return traces.Price(
        **{
            "currency": "USD",
            "as_of": "2026-01-01",
            "valid_until": "2026-12-31",
            "unit": "tokens",
            "input_per_million": "2",
            "output_per_million": "8",
            **changes,
        }
    )


def test_cost_exact_arithmetic_validity_unknown_and_simulation():
    usage = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    prices = {"demo:model": price()}
    day = date(2026, 9, 28)
    result = traces.estimate(prices, "demo", "model", usage, True, day)
    assert result["amount"] == "0.00036" and result["price"]["as_of"] == "2026-01-01"
    assert (
        traces.estimate(prices, "demo", "model", usage, True, date(2027, 1, 1))[
            "reason"
        ]
        == "price_outside_validity"
    )
    for provider, model, data, complete in [
        ("demo", "unknown", usage, True),
        ("demo", "model", None, False),
        ("demo", "model", usage, False),
        ("fake", "model", usage, True),
    ]:
        assert (
            traces.estimate(prices, provider, model, data, complete, day)["amount"]
            is None
        )
    rerank = {"cohere:test": price(unit="search_units", per_search_unit="0.003")}
    assert (
        traces.estimate(rerank, "cohere", "test", {"search_units": 2}, True, day)[
            "amount"
        ]
        == "0.006"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"input_per_million": "-1"},
        {"input_per_million": "NaN"},
        {"valid_until": "2025-01-01"},
        {"as_of": "bad-date"},
        {"output_per_million": None},
    ],
)
def test_invalid_prices_are_rejected(changes):
    with pytest.raises(ValidationError):
        price(**changes)


@pytest.mark.parametrize(
    "config", ['{"secret-sentinel":"invalid"}', "secret-invalid-json"]
)
def test_price_environment_error_is_safe(monkeypatch, config):
    monkeypatch.setenv("TRACE_PRICES", config)
    with pytest.raises(RuntimeError, match="TRACE_PRICES") as error:
        traces.load_settings()
    assert "secret-sentinel" not in str(error.value)


def test_parent_timing_retries_and_tool_event_structure():
    clock = [0.0]
    trace = traces.RequestTrace(
        uuid4().hex, uuid4(), {"demo:model": price()}, clock=lambda: clock[0]
    )
    trace.created_at = trace.created_at.replace(year=2026, month=9, day=28)

    class Reported:
        provider, model, call_count = "demo", "model", 0

        def generate(self, *args):
            self.call_count += 2
            clock[0] += 0.020
            return ChatResult({}, ModelUsage(100, 20, 120), 20.0, 2)

    with trace.stage("retrieval"):
        clock[0] += 0.010
        trace.wrap_factory("embedding", Reported)().generate([], {})
    with trace.stage("agent.tool", kind="tool"):
        clock[0] += 0.005
    payload = trace.finish(200)
    assert payload["total_ms"] == pytest.approx(35)
    assert payload["timings_ms"]["retrieval_non_model"] == pytest.approx(10)
    assert payload["timings_ms"]["model_calls"] == pytest.approx(20)
    assert payload["models"]["embedding"]["calls"][0]["usage_status"] == "partial"
    assert payload["cost"]["estimated_total"] is None
    assert payload["events"][-1]["kind"] == "tool"
    assert payload["events"][1]["parent_event_id"] == payload["events"][0]["event_id"]


def test_rerank_accounting_drops_raw_response_and_fake_usage():
    trace = traces.RequestTrace(uuid4().hex, uuid4())

    class Reported:
        provider, model, call_count = "cohere", "controlled", 0

        def rerank(self, *args):
            self.call_count += 1
            return RerankResult(
                [RerankScore(0, 1.0)], 2, {"secret": "private-raw-response"}
            )

    trace.wrap_factory("rerank", Reported)().rerank("private-query", ["private-doc"])
    payload = trace.finish(200)
    assert payload["models"]["rerank"]["usage"] == {"search_units": 2}
    assert "private-" not in json.dumps(payload)
    fake = traces.RequestTrace(uuid4().hex, uuid4())
    fake.wrap_factory("rerank", FakeReranker)().rerank("q", ["d"])
    assert fake.finish(200)["models"]["rerank"]["usage_status"] == "simulated"


def test_trace_migration_roundtrip_in_disposable_database(retrieval_db):
    engine, _ = retrieval_db
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.check(config)
    command.downgrade(config, "0005_job_leases")
    assert "answer_traces" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert "answer_traces" in inspect(engine).get_table_names()
    command.check(config)


def test_missing_usage_and_call_counter_remain_unknown():
    class Missing:
        provider, model = "demo", "model"

        def generate(self, *args):
            raise ModelError(
                "MODEL_TIMEOUT"
            )  # Default attempts=0 means unreported here.

    trace = traces.RequestTrace(uuid4().hex, uuid4())
    with pytest.raises(ModelError):
        trace.wrap_factory("chat", Missing)().generate([], {})
    payload = trace.finish(504, "MODEL_TIMEOUT")
    assert payload["models"]["chat"]["method_calls"] == 1
    assert payload["models"]["chat"]["call_count"] is None
    assert payload["models"]["chat"]["usage"] is None
    assert payload["cost"]["estimated_total"] is None


def test_unknown_model_response_failure_is_recorded_without_message(retrieval_db):
    engine, ids = retrieval_db
    _indexed(engine, ids["a"])

    class Broken(FakeChatClient):
        def generate(self, *args):
            raise RuntimeError("secret-runtime-message")

    with TestClient(_answer_app(Broken(OUTPUT))) as client:
        result = _ask(client, ids)
        assert (
            result.status_code == 500 and result.headers["X-Trace-Status"] == "stored"
        )
        payload = _get(client, ids, result).json()
        assert payload["error_type"] == "INTERNAL_ERROR"
        assert "secret-runtime-message" not in json.dumps(payload)


def test_mixed_currencies_never_sum_and_missing_price_is_not_free():
    class Reported:
        provider, model, call_count = "demo", "one", 0

        def generate(self, *args):
            self.call_count += 1
            return ChatResult({}, ModelUsage(100, 20, 120), 0.0, 1)

    trace = traces.RequestTrace(
        uuid4().hex, uuid4(), {"demo:one": price(), "demo:two": price(currency="CNY")}
    )
    trace.created_at = trace.created_at.replace(year=2026, month=9, day=28)
    trace.wrap_factory("chat", Reported)().generate([], {})
    second = Reported()
    second.model = "two"
    trace.wrap_factory("chat", lambda: second)().generate([], {})
    payload = trace.finish(200)
    assert payload["models"]["chat"]["call_count"] == 2
    assert payload["models"]["chat"]["usage"]["total_tokens"] == 240
    assert payload["cost"]["estimated_total"] is None
    assert not payload["cost"]["complete"]
    no_price = traces.RequestTrace(uuid4().hex, uuid4())
    no_price.wrap_factory("chat", Reported)().generate([], {})
    assert no_price.finish(200)["cost"]["estimated_total"] is None
