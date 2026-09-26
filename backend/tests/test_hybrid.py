"""Database authorization and source consistency through fusion and degradation."""

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker
from test_retrieval import PROFILE, QueryFake, _seed
from test_retrieval import retrieval_db as retrieval_db

from app.llm.contracts import ModelError
from app.models import Chunk, KBMember
from app.services import hybrid
from app.services.knowledge_bases import NotFound
from app.services.retrieval import RetrievalError


def test_scope_once_per_route_and_permission_revocation(retrieval_db):
    engine, ids = retrieval_db
    keep = _seed(engine, ids["a"], "HTTP", [1.0] + [0.0] * 1535)
    _seed(engine, ids["b"], "HTTP private", [1.0] + [0.0] * 1535)
    _seed(engine, ids["a"], "HTTP deleted", [1.0] + [0.0] * 1535, deleted=True)
    client = QueryFake()
    factory = sessionmaker(engine)
    result = hybrid.search(
        factory, ids["alice"], ids["a"], "HTTP", PROFILE, lambda: client
    )
    assert client.call_count == 1
    assert [c.chunk_id for c in result.items] == [keep]
    assert result.items[0].vector_rank == result.items[0].bm25_rank == 1
    with pytest.raises(NotFound):
        hybrid.search(
            factory,
            ids["alice"],
            ids["b"],
            "HTTP",
            PROFILE,
            lambda: client,
            config=hybrid.FusionConfig(True),
        )
    assert client.call_count == 1

    class RevokeThenTimeout(QueryFake):
        def embed_query(self, text):
            assert engine.pool.checkedout() == 0
            with Session(engine) as session:
                session.execute(
                    delete(KBMember).where(
                        KBMember.user_id == ids["alice"], KBMember.kb_id == ids["a"]
                    )
                )
                session.commit()
            raise ModelError("MODEL_TIMEOUT")

    trace = {}
    with pytest.raises(NotFound):
        hybrid.search(
            factory,
            ids["alice"],
            ids["a"],
            "HTTP",
            PROFILE,
            RevokeThenTimeout,
            config=hybrid.FusionConfig(True),
            trace=trace,
        )
    assert trace["status"] == "error" and trace["routes"]["bm25"]["status"] == "not_run"


def test_stale_vector_candidate_during_bm25_is_rejected(retrieval_db, monkeypatch):
    engine, ids = retrieval_db
    chunk_id = _seed(engine, ids["a"], "HTTP", [1.0] + [0.0] * 1535)
    original = hybrid.bm25.search

    def mutate(*args, **kwargs):
        with Session(engine) as session:
            session.get(Chunk, chunk_id).body = "SMTP"
            session.commit()
        return original(*args, **kwargs)

    monkeypatch.setattr(hybrid.bm25, "search", mutate)
    with pytest.raises(RetrievalError, match="RRF_CORPUS_CHANGED"):
        hybrid.search(
            sessionmaker(engine),
            ids["alice"],
            ids["a"],
            "HTTP",
            PROFILE,
            QueryFake,
            config=hybrid.FusionConfig(True),
        )
