"""Hand-computed ranks and explicit degradation/security boundaries."""

from dataclasses import asdict, replace
from uuid import UUID

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.evaluation.comparison import summarize
from app.retrieval.rrf import fuse
from app.retrieval.vector import RetrievedChunk
from app.services import hybrid
from app.services.knowledge_bases import NotFound
from app.services.retrieval import RetrievalError


def chunk(n, rank, *, distance=0.1, bm25_score=None):
    return RetrievedChunk(
        UUID(int=n),
        UUID(int=n),
        UUID(int=n),
        UUID(int=100),
        f"{n}.md",
        f"正文{n}",
        1,
        ["标题"],
        distance,
        rank,
        bm25_score=bm25_score,
    )


def test_hand_computed_fusion_dedup_and_raw_ranks():
    vector = [chunk(1, 1), chunk(2, 2), chunk(1, 3)]
    lexical = [chunk(2, 1, bm25_score=-10), chunk(3, 2, bm25_score=1000)]
    result = fuse(vector, lexical)
    assert [c.chunk_id.int for c in result] == [2, 1, 3]
    assert [c.rrf_score for c in result] == pytest.approx(
        [1 / 62 + 1 / 61, 1 / 61, 1 / 62]
    )
    assert [(c.vector_rank, c.bm25_rank) for c in result] == [
        (2, 1),
        (1, None),
        (None, 2),
    ]
    assert result[0].bm25_score == -10 and result[0].distance == 0.1
    assert result[2].distance is None
    # Changes in original scores do not affect fusion ranking or RRF scores.
    changed = fuse(
        [replace(c, distance=1000) for c in vector],
        [replace(c, bm25_score=-9999) for c in lexical],
    )
    assert [(c.chunk_id, c.rrf_score) for c in changed] == [
        (c.chunk_id, c.rrf_score) for c in result
    ]


def test_empty_route_stable_ties_and_candidate_cap():
    assert fuse([], []) == []
    assert fuse([chunk(1, 1)], [])[0].rrf_score == pytest.approx(1 / 61)
    assert fuse([], [chunk(1, 1)])[0].bm25_rank == 1
    vector, lexical = [chunk(2, 1), chunk(1, 2)], [chunk(1, 1), chunk(2, 2)]
    assert [c.chunk_id.int for c in fuse(vector, lexical)] == [1, 2]
    assert [
        c.chunk_id.int for c in fuse(list(reversed(vector)), list(reversed(lexical)))
    ] == [1, 2]
    candidates = [chunk(i, i) for i in range(1, 26)]
    assert len(fuse(candidates, [])) == 5
    assert len(fuse(candidates, [], 20)) == 20


@pytest.mark.parametrize("rank", [0, -1, True, 1.5, 21])
def test_invalid_ranks(rank):
    with pytest.raises(ValueError, match="INVALID_RRF_RANK"):
        fuse([chunk(1, rank)], [])


def test_conflicting_source_fails():
    with pytest.raises(ValueError, match="RRF_SOURCE_MISMATCH"):
        fuse([chunk(1, 1)], [replace(chunk(1, 2), text="不同正文")])


@pytest.fixture
def routes(monkeypatch):
    candidate = chunk(1, 1)
    corpus = [asdict(candidate)]
    calls = []
    monkeypatch.setattr(hybrid.bm25, "read_corpus", lambda *args: corpus)

    def vector(*args):
        assert args[4] == 20
        calls.append("vector")
        return [candidate]

    def bm25(*args, trace):
        assert args[4] == 20
        calls.append("bm25")
        trace["cost"] = {}
        return [replace(candidate, distance=None, bm25_score=-1)]

    monkeypatch.setattr(hybrid.retrieval, "search", vector)
    monkeypatch.setattr(hybrid.bm25, "search", bm25)
    return calls


def run(**kwargs):
    return hybrid.search(None, UUID(int=7), UUID(int=100), "问题", None, None, **kwargs)


@pytest.mark.parametrize("route", ["vector", "bm25"])
def test_transient_failure_can_degrade_but_strict_default_fails(
    routes, monkeypatch, route
):
    def failed(*args, **kwargs):
        if route == "vector":
            raise RetrievalError("MODEL_TIMEOUT", 504, "timeout")
        raise PoolTimeout("pool timeout")

    target = hybrid.retrieval if route == "vector" else hybrid.bm25
    monkeypatch.setattr(target, "search", failed)
    with pytest.raises((RetrievalError, PoolTimeout)):
        run()
    result = run(config=hybrid.FusionConfig(True))
    assert result.trace["degraded"] is True
    assert result.trace["routes"][route]["status"] == "transient_failure"
    assert len(result.items) == 1
    assert result.items[0].rrf_score == pytest.approx(1 / 61)
    assert getattr(result.items[0], route + "_rank") is None


@pytest.mark.parametrize(
    "failure",
    [
        NotFound(),
        RetrievalError("MODEL_AUTH_ERROR", 502, "bad key"),
        RetrievalError("MODEL_INVALID_VECTOR", 502, "dimension"),
        RetrievalError("MODEL_TIMEOUT", 403, "permission"),
        RetrievalError("BM25_CORPUS_CHANGED", 409, "changed"),
        RuntimeError("bug"),
    ],
)
def test_non_transient_failures_never_degrade(routes, monkeypatch, failure):
    def failed(*args):
        raise failure

    monkeypatch.setattr(hybrid.retrieval, "search", failed)
    trace = {}
    with pytest.raises(type(failure)):
        run(config=hybrid.FusionConfig(True), trace=trace)
    assert trace["status"] == "error" and not trace["degraded"]
    assert (
        routes == []
    )  # Never enter the remaining route after fatal first-route failure.


def test_database_permission_error_is_not_temporary(routes, monkeypatch):
    class PermissionError(Exception):
        sqlstate = "42501"

    def failed(*args, **kwargs):
        raise DBAPIError("SELECT private", {}, PermissionError("secret detail"))

    monkeypatch.setattr(hybrid.bm25, "search", failed)
    trace = {}
    with pytest.raises(DBAPIError):
        run(config=hybrid.FusionConfig(True), trace=trace)
    assert not trace["degraded"] and "secret detail" not in str(trace)


def test_both_routes_failed_is_not_empty_success(routes, monkeypatch):
    def failed(*args, **kwargs):
        raise PoolTimeout("temporarily busy")

    monkeypatch.setattr(hybrid.retrieval, "search", failed)
    monkeypatch.setattr(hybrid.bm25, "search", failed)
    trace = {}
    with pytest.raises(RetrievalError, match="RRF_ALL_ROUTES_FAILED"):
        run(config=hybrid.FusionConfig(True), trace=trace)
    assert trace["degraded"] and trace["status"] == "error"


def test_zero_or_negative_improvement_is_reported_with_paired_denominator():
    def metrics(value):
        return {"hit_at_5": value, "evidence_recall_at_5": value, "mrr_at_5": value}

    paired = {
        "execution": "complete",
        "comparison_eligible": True,
        "metrics": {"vector": metrics(1), "bm25": metrics(0.5), "rrf": metrics(0.5)},
    }
    excluded = {
        "execution": "complete",
        "comparison_eligible": False,
        "trace": {"degraded": True},
        "metrics": {name: metrics(1) for name in ("vector", "bm25", "rrf")},
    }
    report = summarize([paired, excluded])
    assert report["paired_n"] == report["excluded_n"] == 1
    assert report["rrf_minus"]["vector"]["hit_at_5"] == -0.5
    assert report["rrf_minus"]["bm25"]["hit_at_5"] == 0
    assert summarize([excluded])["rrf_minus"]["vector"]["hit_at_5"] is None


def test_real_comparison_path_uses_same_outputs_and_rejects_index_drift(monkeypatch):
    from app.evaluation import comparison

    candidate = chunk(1, 1)
    calls = []

    def execute(*args, trace, **kwargs):
        calls.append(1)
        trace.update(
            degraded=False,
            routes={
                name: {"status": "ok", "items": [asdict(candidate)]}
                for name in ("vector", "bm25")
            },
        )
        return hybrid.FusionResult(fuse([candidate], [candidate]), trace)

    monkeypatch.setattr(comparison, "search", execute)
    location = {"section_index": 0, "char_start": 0, "char_end": 3}
    question = {
        "id": "synthetic-test",
        "question": "正文",
        "gold_evidence": [
            {
                "document_id": "A-1",
                "section": "标题",
                "quote": "正文1",
                "locations": [location],
            }
        ],
    }
    index = {
        "sha256": "stable",
        "chunk_map": {
            str(candidate.chunk_id): {
                "sample_document_id": "A-1",
                "source_spans": [location],
            }
        },
    }
    row = comparison.run_question(
        question, None, None, None, None, None, index, lambda: "stable", mode="real"
    )
    assert row["comparison_eligible"]
    assert all(
        row["metrics"][name]["hit_at_5"] == 1 for name in ("vector", "bm25", "rrf")
    )
    assert len(calls) == 1
    checks = iter(["stable", "changed"])
    row = comparison.run_question(
        question, None, None, None, None, None, index, lambda: next(checks), mode="real"
    )
    assert row["execution"] == "error" and row["error_code"] == "INDEX_CHANGED"
    assert not row["comparison_eligible"]
