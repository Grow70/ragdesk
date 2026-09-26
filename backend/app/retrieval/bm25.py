"""Deterministic lexical preprocessing and per-request Okapi scoring."""

import re
import unicodedata
from dataclasses import replace
from time import perf_counter

import jieba
from rank_bm25 import BM25Okapi

from app.retrieval.vector import RetrievedChunk

PREPROCESSING_VERSION = "jieba-identifiers-v1"
PARAMETERS = {"k1": 1.5, "b": 0.75, "epsilon": 0.25}
_PARTS = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*|[\u3400-\u9fff]+")


def tokenize(text: str, tokenizer: jieba.Tokenizer) -> list[str]:
    """Same rules for document/query; identifiers are never split by jieba."""
    tokens = []
    for match in _PARTS.finditer(unicodedata.normalize("NFKC", text).casefold()):
        part = match.group()
        if part[0].isascii():
            tokens.append(part)
        else:
            tokens.extend(tokenizer.lcut(part, cut_all=False, HMM=False))
    return tokens


def rank_chunks(
    chunks: list[RetrievedChunk], query: str, top_k: int, stats: dict
) -> list[RetrievedChunk]:
    stats.update(
        corpus_chunks=len(chunks),
        corpus_utf8_bytes=sum(len(c.text.encode("utf-8")) for c in chunks),
        corpus_tokens=0,
        tokenize_ms=0.0,
        index_ms=0.0,
        score_ms=0.0,
        overlap_candidates=0,
    )
    if not chunks:
        return []
    start = perf_counter()
    tokenizer = jieba.Tokenizer()  # Public dictionary only; no request corpus cache.
    query_tokens = tokenize(query, tokenizer)
    corpus = [tokenize(chunk.text, tokenizer) for chunk in chunks]
    stats["tokenize_ms"] = (perf_counter() - start) * 1000
    stats["corpus_tokens"] = sum(map(len, corpus))
    if not query_tokens or not stats["corpus_tokens"]:
        return []
    start = perf_counter()
    index = BM25Okapi(corpus, **PARAMETERS)
    stats["index_ms"] = (perf_counter() - start) * 1000
    start = perf_counter()
    scores = index.get_scores(query_tokens)
    vocabulary = set(query_tokens)
    # Overlap is independent of score sign. Zero/negative matches remain eligible.
    eligible = [i for i, terms in enumerate(corpus) if vocabulary.intersection(terms)]
    stats["overlap_candidates"] = len(eligible)
    eligible.sort(key=lambda i: (-float(scores[i]), str(chunks[i].chunk_id)))
    result = [
        replace(chunks[i], distance=None, bm25_score=float(scores[i]), rank=rank)
        for rank, i in enumerate(eligible[:top_k], 1)
    ]
    stats["score_ms"] = (perf_counter() - start) * 1000
    return result
