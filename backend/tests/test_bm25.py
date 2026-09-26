"""Lexical behavior, signed scores and authorization; no model quality assertions."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import jieba
import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker
from test_retrieval import _seed
from test_retrieval import retrieval_db as retrieval_db

from app.models import Chunk, Document, KBMember
from app.repositories.chunks import lexical_chunks_stmt
from app.retrieval.bm25 import rank_chunks, tokenize
from app.retrieval.vector import RetrievedChunk
from app.services import bm25
from app.services.knowledge_bases import NotFound
from app.services.retrieval import RetrievalError


def item(text, n=1):
    return RetrievedChunk(
        UUID(int=n),
        UUID(int=n),
        UUID(int=n),
        UUID(int=99),
        "同名.md",
        text,
        2,
        ["标题"],
        None,
        0,
    )


def test_fixed_tokenization_preserves_identifiers_numbers_and_case():
    tokenizer = jieba.Tokenizer()
    assert tokenize("HTTP E_AUTH_401 NX-210-P NX-210-P2 429 680.50 SLA", tokenizer) == [
        "http",
        "e_auth_401",
        "nx-210-p",
        "nx-210-p2",
        "429",
        "680.50",
        "sla",
    ]
    assert tokenize("ＨＴＴＰ nx-210-p", tokenizer) == tokenize(
        "http NX-210-P", tokenizer
    )
    assert tokenize("报销：680 元", tokenizer) == ["报销", "680", "元"]


def test_exact_keyword_case_and_model_number_are_distinct():
    chunks = [
        item("NX-210-P HTTP E_AUTH_401", 1),
        item("NX-210-P2 E_AUTH_403", 2),
        item("NX-210 HTTP E_AUTH_404", 3),
    ]
    for query in ("nx-210-p", "E_auth_401"):
        found = rank_chunks(chunks, query, 5, {})
        assert [c.chunk_id for c in found] == [UUID(int=1)]
        assert found[0].distance is None and found[0].bm25_score is not None
        assert found[0].rank == 1 and found[0].page_number == 2
    assert rank_chunks(chunks, "E_AUTH_999", 5, {}) == []


def test_synonyms_are_not_expanded():
    assert rank_chunks([item("休假")], "休假", 5, {})
    assert rank_chunks([item("休假")], "请假", 5, {}) == []


def test_negative_and_zero_scores_do_not_remove_actual_matches():
    negative = rank_chunks([item("http")], "HTTP", 5, {})
    assert len(negative) == 1 and negative[0].bm25_score < 0
    zero = rank_chunks([item("http"), item("smtp", 2)], "http", 5, {})
    assert len(zero) == 1 and zero[0].bm25_score == 0
    assert rank_chunks([item("http")], "smtp", 5, {}) == []


def test_empty_and_punctuation_only_corpora_and_stable_ties():
    for chunks, query in (
        ([], "内容"),
        ([item("！！！")], "内容"),
        ([item("内容")], "？！"),
    ):
        assert rank_chunks(chunks, query, 5, {}) == []
    chunks = [item("http", 2), item("http", 1)]
    first = rank_chunks(chunks, "http", 2, {})
    second = rank_chunks(chunks, "http", 2, {})
    assert first == second and [c.chunk_id for c in first] == [UUID(int=1), UUID(int=2)]


@pytest.mark.parametrize("top_k", [0, 21, True, 1.5])
def test_invalid_top_k(top_k):
    with pytest.raises(RetrievalError, match="INVALID_TOP_K"):
        bm25.search(None, uuid4(), uuid4(), "test", top_k)


def test_scope_empty_kb_and_no_vector_dependency(retrieval_db, monkeypatch):
    engine, ids = retrieval_db
    keep = _seed(engine, ids["a"], "HTTP 保留", None, config_id="different:config")
    _seed(engine, ids["a"], "HTTP deleted", None, deleted=True)
    _seed(engine, ids["a"], "HTTP inactive", None, active=False)
    _seed(engine, ids["a"], "HTTP failed", None, status="failed")
    _seed(engine, ids["b"], "HTTP other KB", None)
    original = bm25.rank_chunks

    def inspect(chunks, *args):
        assert engine.pool.checkedout() == 0
        assert [c.chunk_id for c in chunks] == [keep]
        return original(chunks, *args)

    factory = sessionmaker(engine)
    assert bm25.search(factory, ids["alice"], ids["empty"], "HTTP") == []
    monkeypatch.setattr(bm25, "rank_chunks", inspect)
    trace = {}
    result = bm25.search(factory, ids["alice"], ids["a"], "http", trace=trace)
    assert [c.chunk_id for c in result] == [keep]
    assert result[0].bm25_score is not None
    assert trace["cost"]["corpus_chunks"] == 1
    assert trace["cost"]["total_ms"] >= trace["cost"]["load_ms"]
    assert "chunks.embedding" not in str(lexical_chunks_stmt(ids["alice"], ids["a"]))
    for kb in (ids["b"], uuid4()):
        with pytest.raises(NotFound):
            bm25.search(factory, ids["alice"], kb, "HTTP")


def test_no_cache_after_delete_update_or_member_removal(retrieval_db):
    engine, ids = retrieval_db
    chunk_id = _seed(engine, ids["a"], "HTTP", None)
    factory = sessionmaker(engine)
    assert bm25.search(factory, ids["alice"], ids["a"], "HTTP")
    with Session(engine) as session:
        chunk = session.get(Chunk, chunk_id)
        chunk.body = "SMTP"
        session.commit()
    assert bm25.search(factory, ids["alice"], ids["a"], "HTTP") == []
    assert bm25.search(factory, ids["alice"], ids["a"], "SMTP")
    with Session(engine) as session:
        doc = session.query(Document).filter_by(kb_id=ids["a"]).one()
        doc.deleted_at = datetime.now(timezone.utc)
        session.commit()
    assert bm25.search(factory, ids["alice"], ids["a"], "SMTP") == []
    with Session(engine) as session:
        session.execute(
            delete(KBMember).where(
                KBMember.user_id == ids["alice"], KBMember.kb_id == ids["a"]
            )
        )
        session.commit()
    with pytest.raises(NotFound):
        bm25.search(factory, ids["alice"], ids["a"], "SMTP")


def test_corpus_change_during_scoring_is_error(retrieval_db, monkeypatch):
    engine, ids = retrieval_db
    chunk_id = _seed(engine, ids["a"], "HTTP", None)
    original = bm25.rank_chunks

    def changed(*args):
        result = original(*args)
        with Session(engine) as session:
            session.get(Chunk, chunk_id).body = "SMTP"
            session.commit()
        return result

    monkeypatch.setattr(bm25, "rank_chunks", changed)
    with pytest.raises(RetrievalError, match="BM25_CORPUS_CHANGED"):
        bm25.search(sessionmaker(engine), ids["alice"], ids["a"], "HTTP")
