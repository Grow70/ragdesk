"""Allowlisted request telemetry; no prompts, source text or provider bodies."""

import math
import re
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select

from app.llm.fake import FakeChatClient
from app.llm.openai import OpenAIChatClient
from app.models import AnswerTrace
from app.retrieval.reranker import CohereReranker, FakeReranker
from app.services.knowledge_bases import NotFound, require_kb_member


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    as_of: date
    valid_until: date
    unit: str = Field(pattern=r"^(tokens|search_units)$")
    input_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    per_search_unit: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def complete_price(self):
        if self.valid_until < self.as_of:
            raise ValueError("invalid date interval")
        if self.unit == "tokens" and (
            self.input_per_million is None or self.output_per_million is None
        ):
            raise ValueError("both token prices are required")
        if self.unit == "search_units" and self.per_search_unit is None:
            raise ValueError("search unit price is required")
        return self


class TraceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRACE_", env_file=None, extra="ignore"
    )
    prices: dict[str, Price] = Field(default_factory=dict)


def load_settings():
    try:
        return TraceSettings()
    except (ValidationError, ValueError):
        raise RuntimeError("Missing or invalid configuration: TRACE_PRICES") from None


def error_code(exc):
    if isinstance(exc, NotFound):
        return "NOT_FOUND"
    value = getattr(exc, "code", "INTERNAL_ERROR")
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", value)
        else "INTERNAL_ERROR"
    )


def _integer(value):
    return value if type(value) is int and value >= 0 else None


def _usage(result, role, provider):
    if provider == "fake":
        return None
    if role == "rerank":
        value = getattr(result, "search_units", None)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            return {"search_units": value}
        return None
    raw = getattr(result, "usage", None)
    values = {
        key: _integer(getattr(raw, key, None))
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }
    if (
        None in values.values()
        or values["input_tokens"] + values["output_tokens"] != values["total_tokens"]
    ):
        return None
    return values


def estimate(prices, provider, model, usage, complete, day):
    price = prices.get(f"{provider}:{model}")
    record = {
        "amount": None,
        "currency": price.currency if price else None,
        "price": price.model_dump(mode="json") if price else None,
        "reason": "unknown_price",
    }
    if provider == "fake":
        record["reason"] = "simulated"
    elif not complete or usage is None:
        record["reason"] = "unknown_usage"
    elif price is None:
        pass
    elif not price.as_of <= day <= price.valid_until:
        record["reason"] = "price_outside_validity"
    elif price.unit == "tokens" and "input_tokens" in usage:
        amount = (
            Decimal(usage["input_tokens"]) * price.input_per_million
            + Decimal(usage["output_tokens"]) * price.output_per_million
        ) / Decimal(1_000_000)
        record.update(amount=format(amount, "f"), reason=None)
    elif price.unit == "search_units" and "search_units" in usage:
        record.update(
            amount=format(
                Decimal(str(usage["search_units"])) * price.per_search_unit, "f"
            ),
            reason=None,
        )
    else:
        record["reason"] = "unit_mismatch"
    return record


class RequestTrace:
    def __init__(self, request_id, kb_id, prices=None, *, clock=perf_counter):
        self.request_id, self.kb_id, self.user_id = request_id, kb_id, None
        self.created_at = datetime.now(timezone.utc)
        self.clock, self.start = clock, clock()
        self.prices = prices or {}
        self.events, self.stack = [], []
        self.candidates, self.evidence, self.citations = [], [], []
        self.models = {
            role: {
                "status": "not_run",
                "call_count": 0,
                "method_calls": 0,
                "usage": None,
                "usage_status": "not_run",
                "calls": [],
            }
            for role in ("embedding", "chat", "rerank")
        }
        self.preparation_ms = None
        self.outcome = None
        self.degradations = []

    @contextmanager
    def stage(self, name, *, kind="stage"):
        if kind not in {"stage", "model", "tool"} or not re.fullmatch(
            r"[a-z][a-z0-9_.-]{0,63}", name
        ):
            raise ValueError("invalid event kind/name")
        start = self.clock()
        event = {
            "event_id": f"e{len(self.events) + 1}",
            "parent_event_id": self.stack[-1] if self.stack else None,
            "kind": kind,
            "name": name,
            "offset_ms": (start - self.start) * 1000,
            "duration_ms": None,
            "status": "complete",
            "error_type": None,
        }
        self.events.append(event)
        self.stack.append(event["event_id"])
        try:
            yield event
        except Exception as exc:
            event.update(status="error", error_type=error_code(exc))
            raise
        finally:
            event["duration_ms"] = (self.clock() - start) * 1000
            self.stack.pop()

    def candidates_from(self, chunks):
        self.candidates = [
            {
                key: str(getattr(chunk, key))
                if key.endswith("_id")
                else getattr(chunk, key, None)
                for key in (
                    "chunk_id",
                    "document_id",
                    "build_id",
                    "rank",
                    "vector_rank",
                    "bm25_rank",
                    "rrf_rank",
                )
            }
            for chunk in chunks
        ]

    def evidence_from(self, evidence):
        self.evidence = [
            {"citation_id": key, "chunk_id": str(chunk.chunk_id)}
            for key, chunk in evidence.items()
        ]

    def wrap_factory(self, role, factory):
        def observed():
            return ObservedClient(factory(), self, role)

        return observed

    def invoke(self, role, client, method, args, kwargs):
        provider = getattr(client, "provider", None)
        model = getattr(client, "model", None)
        if isinstance(client, FakeChatClient):
            provider, model = "fake", "controlled-chat-v1"
        elif isinstance(client, OpenAIChatClient):
            provider = "openai"
        elif isinstance(client, FakeReranker):
            provider, model = "fake", "controlled-reranker-v1"
        elif isinstance(client, CohereReranker):
            provider = "cohere"
        # Identifiers are adapter configuration, never copied from provider output.
        before = _integer(getattr(client, "call_count", None))
        result, caught = None, None
        try:
            with self.stage(f"model.{role}", kind="model") as event:
                result = getattr(client, method)(*args, **kwargs)
            return result
        except Exception as exc:
            caught = exc
            raise
        finally:
            after = _integer(getattr(client, "call_count", None))
            count = _integer(getattr(result, "call_count", None))
            if (
                count is None
                and before is not None
                and after is not None
                and after >= before
            ):
                count = after - before
            attempts = _integer(getattr(caught, "attempts", None))
            if attempts:
                count = max(count or 0, attempts)
            usage = _usage(result, role, provider)
            complete = usage is not None and count == 1 and caught is None
            call = {
                "event_id": event["event_id"],
                "provider": provider,
                "model": model,
                "call_count": count,
                "usage": usage,
                "usage_status": "simulated"
                if provider == "fake"
                else ("reported" if complete else "partial" if usage else "unknown"),
                "duration_ms": event["duration_ms"],
                "error_type": event["error_type"],
                "cost": estimate(
                    self.prices,
                    provider,
                    model,
                    usage,
                    complete,
                    self.created_at.date(),
                ),
            }
            slot = self.models[role]
            slot["calls"].append(call)
            slot["method_calls"] += 1
            slot["status"] = (
                "error" if any(c["error_type"] for c in slot["calls"]) else "complete"
            )
            counts = [c["call_count"] for c in slot["calls"]]
            slot["call_count"] = None if None in counts else sum(counts)
            if all(c["usage_status"] == "reported" for c in slot["calls"]):
                slot["usage"] = {
                    k: sum(c["usage"][k] for c in slot["calls"]) for k in usage
                }
                slot["usage_status"] = "reported"
            else:
                slot["usage"] = None
                slot["usage_status"] = "simulated" if provider == "fake" else "unknown"

    def finish(self, status, error=None):
        durations = {
            "request_preparation": self.preparation_ms
            if self.preparation_ms is not None
            else (self.clock() - self.start) * 1000
        }
        for event in self.events:
            if event["kind"] == "stage":
                durations[event["name"]] = (
                    durations.get(event["name"], 0.0) + event["duration_ms"]
                )
        model_ms = sum(e["duration_ms"] for e in self.events if e["kind"] == "model")
        embedding_ms = sum(c["duration_ms"] for c in self.models["embedding"]["calls"])
        if "retrieval" in durations:
            durations["retrieval_non_model"] = max(
                0.0, durations["retrieval"] - embedding_ms
            )
        durations["model_calls"] = model_ms
        costs = [c["cost"] for slot in self.models.values() for c in slot["calls"]]
        known = [c for c in costs if c["amount"] is not None]
        currencies = {c["currency"] for c in known}
        subtotal = (
            format(sum((Decimal(c["amount"]) for c in known), Decimal(0)), "f")
            if len(currencies) == 1
            else None
        )
        return {
            "schema_version": 1,
            "request_id": self.request_id,
            "user_id": str(self.user_id) if self.user_id else None,
            "knowledge_base_id": str(self.kb_id) if self.kb_id else None,
            "created_at": self.created_at.isoformat(),
            "retrieval_strategy": "vector",
            "http_status": status,
            "outcome": self.outcome if status < 400 else "error",
            "refused": self.outcome == "insufficient_evidence"
            or error == "MODEL_REFUSAL",
            "error_type": error,
            "timeout": bool(error and "TIMEOUT" in error),
            "degraded": bool(self.degradations),
            "degradations": self.degradations,
            "candidates": self.candidates,
            "evidence": self.evidence,
            "citations": self.citations,
            "models": self.models,
            "events": self.events,
            "timings_ms": durations,
            "total_ms": (self.clock() - self.start) * 1000,
            "cost": {
                "estimated_total": subtotal
                if len(known) == len(costs) and costs
                else None,
                "known_subtotal": subtotal,
                "currency": next(iter(currencies)) if len(currencies) == 1 else None,
                "complete": bool(costs)
                and len(known) == len(costs)
                and len(currencies) == 1,
            },
        }


class ObservedClient:
    def __init__(self, client, trace, role):
        self.client, self.trace, self.role = client, trace, role

    def __getattr__(self, name):
        if name in {"generate", "embed_query", "embed_documents", "rerank"}:
            return lambda *args, **kwargs: self.trace.invoke(
                self.role, self.client, name, args, kwargs
            )
        return getattr(self.client, name)


def persist(factory, trace, payload):
    with factory.begin() as session:
        session.add(
            AnswerTrace(
                request_id=trace.request_id,
                user_id=trace.user_id,
                knowledge_base_id=trace.kb_id,
                created_at=trace.created_at,
                payload=payload,
            )
        )


def read(session, user_id, kb_id, request_id):
    member = require_kb_member(session, user_id, kb_id)
    row = session.scalar(
        select(AnswerTrace).where(
            AnswerTrace.request_id == request_id, AnswerTrace.knowledge_base_id == kb_id
        )
    )
    if row is None or (row.user_id != user_id and member.role != "admin"):
        raise NotFound()
    return row.payload
