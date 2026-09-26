"""Disposable fake pipeline checks, never stored as a real baseline."""

import hashlib

import pytest
from sqlalchemy.orm import Session, sessionmaker
from test_retrieval import PROFILE, QueryFake, _seed
from test_retrieval import retrieval_db as retrieval_db

from app.evaluation.runtime import bind_index, run_question
from app.llm.contracts import ModelError
from app.llm.fake import FakeChatClient
from app.models import Chunk, DocumentBuild


@pytest.mark.parametrize("fail_chat", [False, True])
def test_observe_same_retrieval_once_and_keep_failures_distinct(
    retrieval_db, fail_chat
):
    engine, ids = retrieval_db
    chunk_id = _seed(engine, ids["a"], "680 元", [1.0] + [0.0] * 1535)
    with Session(engine) as session:
        chunk = session.get(Chunk, chunk_id)
        chunk.source_spans = [{"section_index": 1, "char_start": 0, "char_end": 5}]
        build = session.get(DocumentBuild, chunk.build_id)
        build.embedding_dimensions = 1536
        build.embedding_model = PROFILE.model
        build.embedding_provider = "fake"
        build.config_version = "raw-v1"
        build.chunking_config = {"chunk_size": 600, "overlap": 80}
        session.commit()
    factory = sessionmaker(engine)
    docs = [
        {
            "document_id": "A-1",
            "kb_key": "A",
            "file_sha256": hashlib.sha256("680 元".encode()).hexdigest(),
        }
    ]
    mapping = {"A": str(ids["a"])}

    def snapshot():
        return bind_index(factory, ids["alice"], mapping, docs, {"A"}, PROFILE)

    index = snapshot()
    embedding = QueryFake()

    class Chat(FakeChatClient):
        def generate(self, messages, schema):
            assert engine.pool.checkedout() == 0
            if fail_chat:
                self.call_count += 1
                raise ModelError("MODEL_TIMEOUT", attempts=1)
            return super().generate(messages, schema)

    chat = Chat({"status": "answered", "answer": "680 元 [c1]", "citation_ids": ["c1"]})
    question = {
        "id": "fake-test-1",
        "question": "金额？",
        "category": "direct_fact",
        "answerable": True,
        "expected_facts": ["680 元"],
        "gold_evidence": [
            {
                "document_id": "A-1",
                "section": "限额",
                "quote": "680 元",
                "locations": [{"section_index": 1, "char_start": 0, "char_end": 5}],
            }
        ],
    }
    row = run_question(
        question,
        factory,
        ids["alice"],
        ids["a"],
        PROFILE,
        lambda: embedding,
        lambda: chat,
        index,
        lambda: snapshot()["sha256"],
        execution_mode="fake_test",
    )
    assert embedding.call_count == chat.call_count == 1
    assert row["retrieval_metrics"]["hit_at_5"] == 1
    assert row["retrieval_ms"] >= 0 and row["total_ms"] >= row["retrieval_ms"]
    assert row["calls"][0]["raw_result"]["vectors"]
    assert row["execution_mode"] == "fake_test"
    if fail_chat:
        assert row["execution"] == "error" and row["answer_status"] is None
        assert row["calls"][1]["usage"] is None
    else:
        assert row["citation_valid"] is True
        assert row["trace"]["raw_chat"]["content"]["citation_ids"] == ["c1"]
    with Session(engine) as session:
        chunk = session.get(Chunk, chunk_id)
        chunk.body = "变更后的原文"
        session.commit()
    assert snapshot()["sha256"] != index["sha256"]
    repeated = run_question(
        question,
        factory,
        ids["alice"],
        ids["a"],
        PROFILE,
        lambda: embedding,
        lambda: chat,
        index,
        lambda: snapshot()["sha256"],
        execution_mode="fake_test",
    )
    assert repeated["error_code"] == "INDEX_CHANGED"
    assert embedding.call_count == 1
