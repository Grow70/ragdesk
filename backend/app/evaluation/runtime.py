"""Read-only index binding and one observed call of the existing RAG service."""

from dataclasses import asdict
from time import perf_counter
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app.evaluation.data import canonical, digest
from app.evaluation.metrics import retrieval_metrics
from app.models import Chunk, Document, DocumentBuild, User
from app.services.answers import ContextBudget, answer_question
from app.services.auth import verified_user_id
from app.services.knowledge_bases import require_kb_member


def authenticate(factory, token, secret):
    user_id = verified_user_id(token, secret)
    with factory() as session:
        user = session.get(User, user_id)
        if user is None or not user.login_name or not user.password_hash:
            raise ValueError("INVALID_EVAL_IDENTITY")
    return user_id


def bind_index(factory, user_id, mapping, documents, kb_keys, profile):
    builds, chunk_map = [], {}
    with factory() as session:
        for kb_key in sorted(kb_keys):
            kb_id = UUID(mapping[kb_key])
            require_kb_member(session, user_id, kb_id)
            expected = {
                doc["file_sha256"]: doc for doc in documents if doc["kb_key"] == kb_key
            }
            actual = session.scalars(
                select(Document).where(
                    Document.kb_id == kb_id, Document.deleted_at.is_(None)
                )
            ).all()
            if {doc.file_sha256 for doc in actual} != expected.keys():
                raise ValueError("CORPUS_MISMATCH")
            for document in sorted(actual, key=lambda doc: str(doc.id)):
                build = (
                    session.get(DocumentBuild, document.active_build_id)
                    if document.active_build_id
                    else None
                )
                if (
                    build is None
                    or build.status != "ready"
                    or build.model_config_id != profile.config_id
                    or build.document_id != document.id
                    or build.embedding_dimensions != profile.dimensions
                ):
                    raise ValueError("INDEX_CONFIG_MISMATCH")
                chunks = session.scalars(
                    select(Chunk)
                    .where(Chunk.build_id == build.id)
                    .order_by(Chunk.ordinal)
                ).all()
                if not chunks or any(
                    c.embedding is None or not c.source_spans for c in chunks
                ):
                    raise ValueError("INDEX_MISSING_VECTORS_OR_LOCATORS")
                sample_id = expected[document.file_sha256]["document_id"]
                fingerprints = []
                for chunk in chunks:
                    chunk_map[str(chunk.id)] = {
                        "sample_document_id": sample_id,
                        "source_spans": chunk.source_spans,
                    }
                    fingerprints.append(
                        {
                            "id": chunk.id,
                            "ordinal": chunk.ordinal,
                            "body_hash": digest(chunk.body.encode("utf-8")),
                            "source_spans": chunk.source_spans,
                            "vector": [float(value) for value in chunk.embedding],
                            "page_number": chunk.page_number,
                            "heading_path": chunk.heading_path,
                        }
                    )
                builds.append(
                    {
                        "sample_document_id": sample_id,
                        "kb_key": kb_key,
                        "kb_id": kb_id,
                        "document_id": document.id,
                        "build_id": build.id,
                        "file_sha256": document.file_sha256,
                        "model_config_id": build.model_config_id,
                        "embedding_model": build.embedding_model,
                        "embedding_provider": build.embedding_provider,
                        "embedding_dimensions": build.embedding_dimensions,
                        "config_version": build.config_version,
                        "parser_config": build.parser_config,
                        "chunking_config": build.chunking_config,
                        "chunk_count": len(chunks),
                        "chunks_sha256": digest(canonical(fingerprints)),
                    }
                )
    return {
        "builds": builds,
        "sha256": digest(canonical(builds)),
        "chunk_map": chunk_map,
    }


class RecordedClient:
    """Evaluation-only response capture; never persist request headers."""

    def __init__(self, adapter, *, transport=None, **options):
        self.http_responses = []
        secret = options["api_key"]

        def record(response):
            response.read()
            self.http_responses.append(
                {
                    "status_code": response.status_code,
                    "body": response.text.replace(secret, "[REDACTED_API_KEY]"),
                }
            )

        self.http = httpx.Client(
            transport=transport, event_hooks={"response": [record]}
        )
        try:
            self.client = adapter(http_client=self.http, **options)
        except Exception:
            self.http.close()
            raise

    def __getattr__(self, name):
        return getattr(self.client, name)

    def close(self):
        try:
            self.client.close()
        finally:
            self.http.close()


class ObservedClient:
    def __init__(self, client, calls, kind):
        self.client, self.calls, self.kind = client, calls, kind

    def __getattr__(self, name):
        return getattr(self.client, name)

    def _call(self, method, *args):
        start = perf_counter()
        before = self.client.call_count
        response_start = len(getattr(self.client, "http_responses", []))
        entry = {"kind": self.kind, "usage": None, "usage_complete": False}
        try:
            result = getattr(self.client, method)(*args)
            entry.update(
                usage=asdict(result.usage),
                raw_result=asdict(result),
                usage_complete=result.call_count == 1,
            )
            return result
        except Exception as exc:
            entry["error_code"] = getattr(exc, "code", type(exc).__name__)
            raise
        finally:
            entry["raw_http_responses"] = getattr(self.client, "http_responses", [])[
                response_start:
            ]
            entry["attempts"] = self.client.call_count - before
            entry["elapsed_ms"] = (perf_counter() - start) * 1000
            self.calls.append(entry)

    def embed_query(self, text):
        return self._call("embed_query", text)

    def generate(self, messages, schema):
        return self._call("generate", messages, schema)


def citation_validity(trace, result):
    raw = trace.get("raw_chat", {}).get("content")
    if not isinstance(raw, dict):
        return None
    ids = raw.get("citation_ids", [])
    if raw.get("status") != "answered" and not ids:
        return None
    evidence = trace.get("evidence", {})
    if (
        not isinstance(ids, list)
        or not ids
        or any(not isinstance(value, str) for value in ids)
        or len(set(ids)) != len(ids)
        or not set(ids) <= evidence.keys()
        or result is None
        or result["status"] != "answered"
    ):
        return False
    citations = result["citations"]
    if [c["citation_id"] for c in citations] != ids:
        return False
    return all(
        all(
            str(c[key]) == str(evidence[c["citation_id"]][key])
            for key in ("document_id", "build_id", "chunk_id")
        )
        and c["snippet"] == evidence[c["citation_id"]]["text"]
        for c in citations
    )


def run_question(
    question,
    factory,
    user_id,
    kb_id,
    profile,
    embedding_factory,
    chat_factory,
    index,
    index_check,
    *,
    execution_mode,
):
    row = {
        "id": question["id"],
        "question": question["question"],
        "kb_id": question.get("kb_id"),
        "split": question.get("split"),
        "category": question["category"],
        "answerable": question["answerable"],
        "gold_evidence": question["gold_evidence"],
        "expected_facts": question["expected_facts"],
        "execution": "error",
        "execution_mode": execution_mode,
        "answer_status": None,
        "result": None,
        "retrieval_metrics": None,
        "citation_valid": None,
        "calls": [],
        "manual_review": {
            "factual_correctness": "pending",
            "citation_support": "pending",
        },
    }
    trace = {}
    start = None
    try:
        if index_check() != index["sha256"]:
            raise ValueError("INDEX_CHANGED")
        start = perf_counter()
        result = answer_question(
            factory,
            user_id,
            kb_id,
            question["question"],
            5,
            profile,
            lambda: ObservedClient(embedding_factory(), row["calls"], "embedding"),
            lambda: ObservedClient(chat_factory(), row["calls"], "chat"),
            uuid4().hex,
            ContextBudget(),
            trace=trace,
        )
        row["result"] = asdict(result)
        row.update(execution="complete", answer_status=result.status)
    except Exception as exc:
        # Adapters expose safe codes; never serialize provider exception text.
        row["error_code"] = getattr(
            exc,
            "code",
            "INDEX_CHANGED" if str(exc) == "INDEX_CHANGED" else type(exc).__name__,
        )
    row["total_ms"] = (perf_counter() - start) * 1000 if start is not None else None
    try:
        index_unchanged = index_check() == index["sha256"]
    except Exception:
        index_unchanged = False
    if not index_unchanged:
        row.update(execution="error", answer_status=None, error_code="INDEX_CHANGED")
    retrieved = trace.get("retrieved_chunks")
    if retrieved is not None and index_unchanged:
        for item in retrieved:
            item.update(index["chunk_map"].get(str(item["chunk_id"]), {}))
        row["retrieval_metrics"] = retrieval_metrics(
            question["gold_evidence"], retrieved
        )
    row["retrieval_ms"] = trace.get("retrieval_ms")
    row["citation_valid"] = (
        citation_validity(trace, row["result"]) if index_unchanged else None
    )
    row["trace"] = trace
    return row
