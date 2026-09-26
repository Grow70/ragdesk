"""Cohere v2 adapter; scores rank supplied documents, never create sources."""

from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from typing import Protocol

import httpx


class RerankError(Exception):
    def __init__(self, code: str, *, transient: bool = False):
        super().__init__(code)
        self.code = code
        self.transient = transient


@dataclass(frozen=True)
class RerankScore:
    index: int
    score: float


@dataclass(frozen=True)
class RerankResult:
    scores: list[RerankScore]
    search_units: float | None = None
    raw_response: dict | None = None


class Reranker(Protocol):
    def rerank(self, query: str, documents: list[str]) -> RerankResult: ...

    def close(self) -> None: ...


def validate_scores(result: RerankResult, count: int) -> list[RerankScore]:
    """Require a full permutation; do not silently discard malformed candidates."""
    if not isinstance(result, RerankResult) or len(result.scores) != count:
        raise RerankError("RERANK_INVALID_RESPONSE")
    seen = set()
    for item in result.scores:
        if (
            not isinstance(item, RerankScore)
            or type(item.index) is not int
            or not 0 <= item.index < count
            or item.index in seen
            or type(item.score) not in (float, int)
            or not isfinite(item.score)
        ):
            raise RerankError("RERANK_INVALID_RESPONSE")
        seen.add(item.index)
    if result.search_units is not None and (
        type(result.search_units) not in (float, int)
        or not isfinite(result.search_units)
        or result.search_units < 0
    ):
        raise RerankError("RERANK_INVALID_RESPONSE")
    return sorted(result.scores, key=lambda item: (-item.score, item.index))


class CohereReranker:
    """One request, no retries, no document/credential logging or SDK fallback."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "rerank-v3.5",
        connect_timeout: float = 3,
        read_timeout: float = 10,
        transport=None,
    ):
        if not api_key or not api_key.strip() or model != "rerank-v3.5":
            raise RerankError("RERANK_INVALID_CONFIG")
        if any(
            type(v) not in (float, int) or not isfinite(v) or v <= 0
            for v in (connect_timeout, read_timeout)
        ):
            raise RerankError("RERANK_INVALID_CONFIG")
        self.model = model
        self.call_count = 0
        self.elapsed_ms = 0.0
        self.client = httpx.Client(
            base_url="https://api.cohere.com",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=read_timeout,
                pool=connect_timeout,
            ),
            transport=transport,
            follow_redirects=False,
        )

    def close(self):
        self.client.close()

    def rerank(self, query: str, documents: list[str]) -> RerankResult:
        if (
            not isinstance(query, str)
            or not query.strip()
            or not 1 <= len(documents) <= 20
            or any(not isinstance(d, str) or not d.strip() for d in documents)
        ):
            raise RerankError("RERANK_INVALID_INPUT")
        start = perf_counter()
        self.call_count += 1
        try:
            try:
                response = self.client.post(
                    "/v2/rerank",
                    json={
                        "model": self.model,
                        "query": query,
                        "documents": documents,
                        "top_n": len(documents),
                        "max_tokens_per_doc": 4096,
                    },
                )
            except httpx.TimeoutException:
                raise RerankError("RERANK_TIMEOUT", transient=True) from None
            except httpx.TransportError:
                raise RerankError("RERANK_UNAVAILABLE", transient=True) from None
            status = response.status_code
            if status in (408, 429) or 500 <= status <= 599:
                raise RerankError("RERANK_UNAVAILABLE", transient=True)
            if status in (401, 403, 498):
                raise RerankError("RERANK_AUTH_FAILED")
            if status != 200:
                raise RerankError("RERANK_REQUEST_REJECTED")
            try:
                body = response.json()
                result = RerankResult(
                    scores=[
                        RerankScore(item["index"], item["relevance_score"])
                        for item in body["results"]
                    ],
                    search_units=(body.get("meta") or {})
                    .get("billed_units", {})
                    .get("search_units"),
                    raw_response=body,
                )
                validate_scores(result, len(documents))
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                raise RerankError("RERANK_INVALID_RESPONSE") from None
            return result
        finally:
            self.elapsed_ms = (perf_counter() - start) * 1000


class FakeReranker:
    """Deterministic, explicitly selected diagnostic client; no semantic claims."""

    def __init__(self, scores=None, error: RerankError | None = None):
        self.scores = scores
        self.error = error
        self.call_count = 0

    def rerank(self, query: str, documents: list[str]) -> RerankResult:
        self.call_count += 1
        if self.error:
            raise self.error
        scores = self.scores
        if scores is None:
            scores = [RerankScore(i, float(i)) for i in range(len(documents))]
        return RerankResult(scores, raw_response={"fake": True})

    def close(self):
        pass
