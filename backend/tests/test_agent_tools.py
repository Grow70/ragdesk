"""Direct read-only tool checks; real PostgreSQL authorization, fake models."""

import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_retrieval import PROFILE, QueryFake, _seed
from test_retrieval import retrieval_db as retrieval_db

from app.agent.contracts import RunContext, ToolLimits, ToolResult, tool_definitions
from app.agent.tools import KnowledgeTools
from app.llm.contracts import ModelError
from app.models import Chunk, Document, DocumentBuild, KBMember
from app.services import retrieval
from app.services.traces import RequestTrace

VECTOR = [1.0] + [0.0] * 1535


def _tools(
    engine,
    ids,
    *,
    kb="a",
    user="alice",
    client=None,
    limits=None,
    context=None,
    trace=None,
):
    return KnowledgeTools(
        context or RunContext(ids[user], ids[kb], uuid4().hex),
        sessionmaker(engine),
        PROFILE,
        lambda: client or QueryFake(),
        limits=limits,
        trace=trace,
    )


def _member_remove(engine, user, kb):
    with Session(engine) as session:
        member = session.scalar(
            select(KBMember).where(KBMember.user_id == user, KBMember.kb_id == kb)
        )
        session.delete(member)
        session.commit()


def test_search_then_read_and_backend_context_is_not_a_tool_argument(retrieval_db):
    engine, ids = retrieval_db
    text = "演示数据：报销上限 680 CNY，超过 7 天需审批。"
    chunk_id = _seed(engine, ids["a"], text, VECTOR)
    fake = QueryFake()
    tools = _tools(engine, ids, client=fake)
    found = tools.search_knowledge("报销")
    assert found["status"] == "success"
    assert found["items"][0]["chunk_id"] == str(chunk_id)
    assert found["items"][0]["distance"] == 0.0
    assert found["items"][0]["rank"] == 1
    read = tools.read_chunks([str(chunk_id)])
    assert read["status"] == "success" and read["items"][0]["text"] == text
    assert read["items"][0]["page_number"] == 1 and read["items"][0][
        "heading_path"
    ] == ["演示标题"]
    assert read["request_id"] == tools.context.request_id
    assert fake.call_count == 1  # Reading never creates another model request.
    assert read["remaining_text_chars"] == 12000 - 2 * len(text)
    definitions = tool_definitions()
    assert {d["name"] for d in definitions} == {"search_knowledge", "read_chunks"}
    for definition in definitions:
        schema = definition["parameters"]
        assert schema["additionalProperties"] is False
        assert (
            not {"user_id", "kb_id", "context", "model", "limits"}
            & schema["properties"].keys()
        )
        Draft202012Validator(definition["returns"]).validate(found)
        Draft202012Validator(definition["returns"]).validate(read)
    with pytest.raises(FrozenInstanceError):
        tools.context.kb_id = ids["b"]
    with pytest.raises(AttributeError):
        tools.context = RunContext(ids["bob"], ids["b"], uuid4().hex)


def test_forged_foreign_unretrieved_and_other_run_ids_all_denied(retrieval_db):
    engine, ids = retrieval_db
    near = _seed(engine, ids["a"], "A private fact", VECTOR)
    other = _seed(engine, ids["a"], "A not returned", [0.0, 1.0] + [0.0] * 1534)
    foreign = _seed(engine, ids["b"], "B private fact", VECTOR)
    tools = _tools(engine, ids)
    tools.search_knowledge("q", top_k=1)
    for bad in (other, foreign, uuid4()):
        result = tools.read_chunks([str(near), str(bad)])
        assert (
            result["status"] == "permission_denied"
            and result["error"]["code"] == "CHUNK_NOT_AVAILABLE"
        )
        assert not result["items"]
        assert "B private fact" not in json.dumps(result)
    assert _tools(engine, ids).read_chunks([str(near)])["status"] == "permission_denied"
    assert (
        tools.call("search_knowledge", {"query": "q", "kb_id": str(ids["b"])})["status"]
        == "invalid_arguments"
    )
    assert (
        tools.call(
            "read_chunks", {"chunk_ids": [str(near)], "user_id": str(ids["bob"])}
        )["status"]
        == "invalid_arguments"
    )
    assert (
        _tools(engine, ids, kb="b").search_knowledge("q")["status"]
        == "permission_denied"
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "x" * 4001},
        {"query": " " * 4000 + "x"},
        {"query": "   "},
        {"query": 123},
        {"query": "q", "top_k": 21},
        {"query": "q", "top_k": 0},
        {"query": "q", "top_k": True},
        {"query": "q", "top_k": "5"},
    ],
)
def test_invalid_search_arguments_never_call_model(retrieval_db, arguments):
    engine, ids = retrieval_db
    _seed(engine, ids["a"], "some text", VECTOR)
    fake = QueryFake()
    result = _tools(engine, ids, client=fake).call("search_knowledge", arguments)
    assert result["status"] == "invalid_arguments" and not result["items"]
    assert fake.call_count == 0
    assert len(json.dumps(result)) < 500


def test_read_schema_duplicates_limits_and_unknown_tools(retrieval_db):
    engine, ids = retrieval_db
    chunk = _seed(engine, ids["a"], "fact", VECTOR)
    tools = _tools(engine, ids)
    tools.search_knowledge("q")
    for values in (
        [],
        [str(chunk)] * 2,
        [str(chunk), str(chunk).upper()],
        [str(uuid4()) for _ in range(21)],
        ["not-uuid"],
        "not-an-array",
    ):
        assert tools.read_chunks(values)["status"] == "invalid_arguments"
    for tool in ("shell", "sql", "open_url", "__dict__"):
        result = tools.call(tool, {"command": "private-sentinel"})
        assert (
            result["status"] == "invalid_arguments"
            and result["error"]["code"] == "UNKNOWN_TOOL"
        )
        assert "private-sentinel" not in json.dumps(result)


def test_removed_member_denied_on_all_calls_even_invalid_arguments(retrieval_db):
    engine, ids = retrieval_db
    chunk = _seed(engine, ids["a"], "fact", VECTOR)
    fake = QueryFake()
    tools = _tools(engine, ids, client=fake)
    tools.search_knowledge("q")
    _member_remove(engine, ids["alice"], ids["a"])
    for result in (
        tools.read_chunks([str(chunk)]),
        tools.search_knowledge("q"),
        tools.search_knowledge("q", 999),
    ):
        assert result["status"] == "permission_denied" and not result["items"]
    assert fake.call_count == 1


def test_revocation_during_embedding_never_returns_text(retrieval_db):
    engine, ids = retrieval_db
    _seed(engine, ids["a"], "fact", VECTOR)

    class RemovingFake(QueryFake):
        def embed_query(self, query):
            assert engine.pool.checkedout() == 0
            _member_remove(engine, ids["alice"], ids["a"])
            return super().embed_query(query)

    result = _tools(engine, ids, client=RemovingFake()).search_knowledge("q")
    assert result["status"] == "permission_denied" and not result["items"]


@pytest.mark.parametrize("change", ["delete", "rebuild", "body"])
def test_read_revalidates_active_build_and_never_maps_old_ids(retrieval_db, change):
    engine, ids = retrieval_db
    chunk_id = _seed(engine, ids["a"], "old fact", VECTOR)
    tools = _tools(engine, ids)
    tools.search_knowledge("q")
    with Session(engine) as session:
        chunk = session.get(Chunk, chunk_id)
        build = session.get(DocumentBuild, chunk.build_id)
        doc = session.get(Document, build.document_id)
        if change == "delete":
            doc.deleted_at = datetime.now(timezone.utc)
        elif change == "body":
            chunk.body = "changed fact"
        else:
            new_id = uuid4()
            session.add(
                DocumentBuild(
                    id=new_id,
                    document_id=doc.id,
                    status="ready",
                    parser_config={},
                    chunking_config={},
                    model_config_id=PROFILE.config_id,
                )
            )
            session.flush()
            session.add(
                Chunk(
                    build_id=new_id,
                    ordinal=0,
                    body="new fact",
                    page_number=1,
                    content_sha256=hashlib.sha256(b"new fact").hexdigest(),
                    embedding=VECTOR,
                )
            )
            doc.active_build_id = new_id
        session.commit()
    read = tools.read_chunks([str(chunk_id)])
    assert (
        read["status"] == "no_results" and read["error"]["code"] == "EVIDENCE_EXPIRED"
    )
    assert not read["items"] and "new fact" not in json.dumps(read)
    if change == "rebuild":
        current = tools.search_knowledge("q")
        assert current["items"][0]["build_id"] == str(new_id)
        assert current["items"][0]["chunk_id"] != str(chunk_id)


def test_deletion_after_search_is_rechecked_before_exposing_candidates(
    retrieval_db, monkeypatch
):
    engine, ids = retrieval_db
    chunk_id = _seed(engine, ids["a"], "private fact", VECTOR)
    original = retrieval.search

    def changing(*args, **kwargs):
        chunks = original(*args, **kwargs)
        with Session(engine) as session:
            build = session.get(DocumentBuild, session.get(Chunk, chunk_id).build_id)
            session.get(Document, build.document_id).deleted_at = datetime.now(
                timezone.utc
            )
            session.commit()
        return chunks

    monkeypatch.setattr(retrieval, "search", changing)
    result = _tools(engine, ids).search_knowledge("q")
    assert result["status"] == "no_results" and not result["items"]


def test_new_search_replaces_permits_on_success_error_and_empty(
    retrieval_db, monkeypatch
):
    engine, ids = retrieval_db
    near = _seed(engine, ids["a"], "near", VECTOR)
    far = _seed(engine, ids["a"], "far", [0.0, 1.0] + [0.0] * 1534)
    tools = _tools(engine, ids)
    assert len(tools.search_knowledge("q", 2)["items"]) == 2
    assert tools.read_chunks([str(far)])["status"] == "success"
    tools.search_knowledge("q", 1)
    assert tools.read_chunks([str(far)])["status"] == "permission_denied"
    tools.search_knowledge("q", 21)
    assert tools.read_chunks([str(near)])["status"] == "permission_denied"
    tools.search_knowledge("q")
    monkeypatch.setattr(retrieval, "search", lambda *args, **kwargs: [])
    assert tools.search_knowledge("q")["status"] == "no_results"
    assert tools.read_chunks([str(near)])["status"] == "permission_denied"


def test_empty_library_and_technical_failure_are_distinct(retrieval_db, monkeypatch):
    engine, ids = retrieval_db
    fake = QueryFake()
    assert (
        _tools(engine, ids, kb="empty", client=fake).search_knowledge("q")["status"]
        == "no_results"
    )
    assert fake.call_count == 0
    chunk = _seed(engine, ids["a"], "fact", VECTOR)
    tools = _tools(engine, ids)
    tools.search_knowledge("q")

    def timeout(*args, **kwargs):
        raise ModelError("MODEL_TIMEOUT", attempts=2)

    monkeypatch.setattr(retrieval, "search", timeout)
    result = tools.search_knowledge("q")
    assert (
        result["status"] == "technical_failure"
        and result["error"]["code"] == "MODEL_TIMEOUT"
    )
    assert result["error"]["retryable"] and not result["items"]
    assert tools.read_chunks([str(chunk)])["status"] == "permission_denied"

    def failed(*args, **kwargs):
        raise RuntimeError("api-key-private-sentinel")

    monkeypatch.setattr(retrieval, "search", failed)
    result = tools.search_knowledge("q")
    assert result["status"] == "technical_failure"
    assert "api-key-private-sentinel" not in json.dumps(result)


def test_output_budget_bounds_json_text_and_only_registers_returned_ids(retrieval_db):
    engine, ids = retrieval_db
    chunk_ids = [_seed(engine, ids["a"], f"long-{i}", VECTOR) for i in range(6)]
    full_text = '演示条款：金额 680 CNY。\n\\"' * 300
    with Session(engine) as session:
        for chunk_id in chunk_ids:
            chunk = session.get(Chunk, chunk_id)
            chunk.body = full_text
            chunk.heading_path = ["超长标题" * 100] * 10
        session.commit()
    fake = QueryFake()
    tools = _tools(
        engine,
        ids,
        client=fake,
        limits=ToolLimits(response_chars=2000, total_text_chars=900),
    )
    search = tools.search_knowledge("q", 6)
    assert search["status"] == "success" and search["truncated"]
    assert len(ToolResult.model_validate(search).model_dump_json()) <= 2000
    returned = {item["chunk_id"] for item in search["items"]}
    omitted = set(map(str, chunk_ids)) - returned
    assert omitted
    assert tools.read_chunks([next(iter(omitted))])["status"] == "permission_denied"
    consumed = sum(len(item["text"]) for item in search["items"])
    while consumed < 900:
        read = tools.read_chunks([next(iter(returned))])
        assert read["status"] == "success"
        assert len(ToolResult.model_validate(read).model_dump_json()) <= 2000
        for item in read["items"]:
            assert full_text.startswith(item["text"])
            assert "text" in item["truncated_fields"]
            assert len(item["document_name"]) <= 160
            assert sum(map(len, item["heading_path"])) <= 256
        consumed += sum(len(item["text"]) for item in read["items"])
    assert consumed == 900
    assert tools.search_knowledge("q")["status"] == "budget_exceeded"
    assert fake.call_count == 1


def test_tool_trace_is_optional_and_never_copies_arguments_or_text(retrieval_db):
    engine, ids = retrieval_db
    chunk = _seed(engine, ids["a"], "private-source-sentinel", VECTOR)
    context = RunContext(ids["alice"], ids["a"], uuid4().hex)
    trace = RequestTrace(context.request_id, context.kb_id)
    trace.user_id = context.user_id
    tools = _tools(engine, ids, context=context, trace=trace)
    tools.search_knowledge("private-query-sentinel")
    tools.read_chunks([str(chunk)])
    snapshot = trace.finish(200)
    assert [e["name"] for e in snapshot["events"] if e["kind"] == "tool"] == [
        "tool.search_knowledge",
        "tool.read_chunks",
    ]
    assert snapshot["models"]["embedding"]["call_count"] == 1
    assert "private-" not in json.dumps(snapshot)
    with pytest.raises(ValueError, match="Trace identity"):
        _tools(engine, ids, trace=trace)
