"""Contract verification uses controlled HTTP, not real ranking evidence."""

import json
import os

import httpx
import pytest

from app.retrieval.reranker import (
    CohereReranker,
    RerankError,
    RerankResult,
    RerankScore,
    validate_scores,
)


def test_http_request_response_and_timeouts(caplog):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url == "https://api.cohere.com/v2/rerank"
        assert request.headers["authorization"] == "Bearer test-secret"
        assert request.extensions["timeout"] == {
            "connect": 2,
            "read": 4,
            "write": 4,
            "pool": 2,
        }
        assert json.loads(request.content) == {
            "model": "rerank-v3.5",
            "query": "报销",
            "documents": ["金额 200", "型号 X-3"],
            "top_n": 2,
            "max_tokens_per_doc": 4096,
        }
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.7},
                    {"index": 0, "relevance_score": 0.9},
                ],
                "meta": {"billed_units": {"search_units": 1}},
                "id": "request-123",
            },
        )

    client = CohereReranker(
        "test-secret",
        connect_timeout=2,
        read_timeout=4,
        transport=httpx.MockTransport(respond),
    )
    try:
        result = client.rerank("报销", ["金额 200", "型号 X-3"])
        assert [s.index for s in validate_scores(result, 2)] == [0, 1]
        assert result.search_units == 1 and result.raw_response["id"] == "request-123"
        assert client.call_count == len(requests) == 1
        assert client.elapsed_ms >= 0
        assert "test-secret" not in caplog.text and "金额 200" not in caplog.text
    finally:
        client.close()


@pytest.mark.parametrize(
    "status,code,transient",
    [
        (401, "RERANK_AUTH_FAILED", False),
        (403, "RERANK_AUTH_FAILED", False),
        (498, "RERANK_AUTH_FAILED", False),
        (422, "RERANK_REQUEST_REJECTED", False),
        (429, "RERANK_UNAVAILABLE", True),
        (503, "RERANK_UNAVAILABLE", True),
    ],
)
def test_http_error_no_retries(status, code, transient):
    client = CohereReranker(
        "key",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(status, text="private body")
        ),
    )
    try:
        with pytest.raises(RerankError) as caught:
            client.rerank("q", ["d"])
        assert caught.value.code == code and caught.value.transient is transient
        assert client.call_count == 1
        assert "private" not in str(caught.value)
    finally:
        client.close()


def test_timeout_is_transient_without_sleep():
    def respond(request):
        raise httpx.ReadTimeout(
            "secret and document should not escape", request=request
        )

    client = CohereReranker("key", transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(RerankError, match="^RERANK_TIMEOUT$") as caught:
            client.rerank("q", ["d"])
        assert caught.value.transient and client.call_count == 1
    finally:
        client.close()


@pytest.mark.parametrize(
    "scores",
    [
        [],
        [RerankScore(0, 1)],
        [RerankScore(0, 1), RerankScore(0, 0.2)],
        [RerankScore(0, 1), RerankScore(2, 0.2)],
        [RerankScore(-1, 1), RerankScore(1, 0.2)],
        [RerankScore(True, 1), RerankScore(0, 0.2)],
        [RerankScore(0, float("nan")), RerankScore(1, 0.2)],
        [RerankScore(0, float("inf")), RerankScore(1, 0.2)],
        [RerankScore(0, True), RerankScore(1, 0.2)],
    ],
)
def test_candidate_permutation_and_finite_scores(scores):
    with pytest.raises(RerankError, match="INVALID_RESPONSE"):
        validate_scores(RerankResult(scores), 2)


def test_equal_scores_preserve_rrf_order_and_missing_usage_is_unknown():
    result = RerankResult([RerankScore(1, 0.5), RerankScore(0, 0.5)])
    assert [r.index for r in validate_scores(result, 2)] == [0, 1]
    assert result.search_units is None


@pytest.mark.parametrize(
    "body", ["not JSON", '{"results": [{}]}', '{"results": [], "meta": []}']
)
def test_malformed_responses_are_hard_failures(body):
    client = CohereReranker(
        "key", transport=httpx.MockTransport(lambda req: httpx.Response(200, text=body))
    )
    try:
        with pytest.raises(RerankError, match="INVALID_RESPONSE") as caught:
            client.rerank("q", ["d"])
        assert not caught.value.transient
    finally:
        client.close()


@pytest.mark.skipif(
    os.getenv("RUN_REAL_RERANK") != "1" or not os.getenv("COHERE_API_KEY"),
    reason="Real Cohere execution not configured",
)
def test_real_cohere_chinese_contract():
    client = CohereReranker(os.environ["COHERE_API_KEY"])
    try:
        result = client.rerank(
            "差旅住宿每天限额是多少？",
            [
                "演示数据：差旅住宿每天限额 300 元。",
                "演示数据：产品编号 RD-42，支持 TXT。",
            ],
        )
        assert len(validate_scores(result, 2)) == 2
        # This smoke check verifies the API contract, not dev-set improvement.
        assert client.call_count == 1
        print(
            json.dumps(
                {
                    "model": client.model,
                    "calls": client.call_count,
                    "elapsed_ms": client.elapsed_ms,
                    "search_units": result.search_units,
                    "results": result.raw_response,
                },
                ensure_ascii=False,
            )
        )
    finally:
        client.close()
