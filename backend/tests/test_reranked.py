"""Verify candidate containment, opt-out, fallback and database authorization."""

from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker
from test_retrieval import PROFILE, QueryFake, _seed
from test_retrieval import retrieval_db as retrieval_db

from app.models import Chunk, KBMember
from app.retrieval.reranker import FakeReranker, RerankError, RerankScore
from app.retrieval.vector import RetrievedChunk
from app.services import reranked
from app.services.hybrid import FusionResult
from app.services.knowledge_bases import NotFound
from app.services.retrieval import RetrievalError


@pytest.fixture
def candidates(monkeypatch):
    items = [
        RetrievedChunk(
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            f"{i}.md",
            f"段落 {i}",
            1,
            ["标题"],
            0.1,
            i + 1,
            rrf_score=1 / (61 + i),
        )
        for i in range(20)
    ]

    def fused(*args, **kwargs):
        assert kwargs["top_k"] == 20
        kwargs["trace"].update(degraded=False, total_ms=1)
        return FusionResult(items, kwargs["trace"])

    monkeypatch.setattr(reranked.hybrid, "search", fused)
    monkeypatch.setattr(
        reranked.bm25, "read_corpus", lambda *a: [asdict(c) for c in items]
    )
    return items


def call(client, **kwargs):
    return reranked.search(
        None, None, None, "查询", PROFILE, QueryFake, lambda: client, **kwargs
    )


def test_twenty_to_five_only_existing_candidates(candidates):
    client = FakeReranker()
    result = call(client, enabled=True)
    assert [i.chunk_id for i in result.items] == [
        i.chunk_id for i in reversed(candidates[-5:])
    ]
    assert [i.rank for i in result.items] == [1, 2, 3, 4, 5]
    assert [i.rrf_rank for i in result.items] == [20, 19, 18, 17, 16]
    assert result.items[0].rrf_score == candidates[-1].rrf_score
    assert client.call_count == 1 and not result.trace["degraded"]
    assert result.trace["rerank"]["search_units"] is None


def test_disabled_setting_never_creates_client(candidates):
    settings = SimpleNamespace(rerank_enabled=False)  # no key/model fields needed
    result = reranked.search_configured(
        None, None, None, "q", PROFILE, QueryFake, settings
    )
    assert [i.chunk_id for i in result.items] == [i.chunk_id for i in candidates[:5]]
    assert result.trace["rerank"]["status"] == "disabled"
    assert result.trace["rerank"]["call_count"] == 0


def test_enabled_setting_requires_key(candidates):
    with pytest.raises(RerankError, match="RERANK_INVALID_CONFIG"):
        reranked.search_configured(
            None,
            None,
            None,
            "q",
            PROFILE,
            QueryFake,
            SimpleNamespace(rerank_enabled=True, cohere_api_key=None),
        )


def test_timeout_falls_back_to_exact_rrf_order(candidates):
    result = call(
        FakeReranker(error=RerankError("RERANK_TIMEOUT", transient=True)), enabled=True
    )
    assert [i.chunk_id for i in result.items] == [i.chunk_id for i in candidates[:5]]
    assert all(i.rerank_score is None for i in result.items)
    assert result.trace["degraded"] and result.trace["rerank"]["status"] == "fallback"
    assert result.trace["rerank"]["error_code"] == "RERANK_TIMEOUT"


@pytest.mark.parametrize(
    "client",
    [
        FakeReranker(scores=[RerankScore(20, 1)] * 20),
        FakeReranker(error=RerankError("RERANK_AUTH_FAILED")),
    ],
)
def test_invalid_source_or_auth_failure_is_not_fallback(candidates, client):
    trace = {}
    with pytest.raises(RerankError):
        call(client, enabled=True, trace=trace)
    assert trace["status"] == "error" and not trace["degraded"]


def test_empty_kb_and_unauthorized_kb_make_no_rerank_call(retrieval_db):
    engine, ids = retrieval_db
    client = FakeReranker()
    factory = sessionmaker(engine)
    result = reranked.search(
        factory,
        ids["alice"],
        ids["empty"],
        "q",
        PROFILE,
        QueryFake,
        lambda: client,
        enabled=True,
    )
    assert result.items == [] and result.trace["rerank"]["status"] == "empty"
    with pytest.raises(NotFound):
        reranked.search(
            factory,
            ids["alice"],
            ids["b"],
            "q",
            PROFILE,
            QueryFake,
            lambda: client,
            enabled=True,
        )
    assert client.call_count == 0


@pytest.mark.parametrize("mutation", ["revoke", "change_source"])
@pytest.mark.parametrize("timeout", [False, True])
def test_revalidate_after_network_call_even_on_fallback(
    retrieval_db, mutation, timeout
):
    engine, ids = retrieval_db
    chunk_id = _seed(engine, ids["a"], "HTTP authorized", [1.0] + [0.0] * 1535)
    _seed(engine, ids["b"], "HTTP private", [1.0] + [0.0] * 1535)

    class MutatingClient(FakeReranker):
        def rerank(self, query, documents):
            assert documents == ["HTTP authorized"]
            assert engine.pool.checkedout() == 0
            with Session(engine) as session:
                if mutation == "revoke":
                    session.execute(
                        delete(KBMember).where(
                            KBMember.user_id == ids["alice"], KBMember.kb_id == ids["a"]
                        )
                    )
                else:
                    session.get(Chunk, chunk_id).body = "replacement"
                session.commit()
            if timeout:
                raise RerankError("RERANK_TIMEOUT", transient=True)
            return super().rerank(query, documents)

    trace = {}
    with pytest.raises(NotFound if mutation == "revoke" else RetrievalError):
        reranked.search(
            sessionmaker(engine),
            ids["alice"],
            ids["a"],
            "HTTP",
            PROFILE,
            QueryFake,
            MutatingClient,
            enabled=True,
            trace=trace,
        )
    assert trace["status"] == "error"
