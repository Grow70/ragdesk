"""Shared request-attempt accounting and a cooperative cancellation deadline."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from threading import Event, Lock
from time import monotonic

from app.llm.contracts import ModelError

ACTIVE_BUDGET = ContextVar("ragdesk_model_budget", default=None)
_FINAL = ContextVar("ragdesk_final_generation", default=False)


class BudgetExceeded(ModelError):
    pass


@dataclass(frozen=True, slots=True)
class RoundLimits:
    tool_calls: int = 3
    model_requests: int = 6
    seconds: float = 60.0
    context_chars: int = 24000

    def __post_init__(self):
        for value, limit in (
            (self.tool_calls, 3),
            (self.model_requests, 6),
            (self.context_chars, 24000),
        ):
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError("Round limits may only reduce the hard limits")
        if (
            type(self.seconds) not in (int, float)
            or not isfinite(self.seconds)
            or not 0 < self.seconds <= 60
        ):
            raise ValueError("Round seconds must be in (0, 60]")


class RequestBudget:
    def __init__(self, limits=RoundLimits(), *, clock=monotonic):
        self.limits, self.clock = limits, clock
        self.deadline = clock() + limits.seconds
        self.wall_deadline = monotonic() + limits.seconds
        self.cancelled = Event()
        self._lock = Lock()
        self.requests = self.tools = self.context_chars = 0
        self.history = []

    def remaining(self):
        seconds = min(self.deadline - self.clock(), self.wall_deadline - monotonic())
        if self.cancelled.is_set() or seconds <= 0:
            raise BudgetExceeded("AGENT_DEADLINE_TIMEOUT")
        return seconds

    def claim_request(self):
        with self._lock:
            self.remaining()
            reserve = 0 if _FINAL.get() else 1
            if self.requests >= self.limits.model_requests - reserve:
                raise BudgetExceeded("AGENT_MODEL_BUDGET")
            self.requests += 1

    def claim_tool(self):
        with self._lock:
            self.remaining()
            if self.tools >= self.limits.tool_calls:
                raise BudgetExceeded("AGENT_TOOL_BUDGET")
            self.tools += 1

    def add_context(self, size):
        with self._lock:
            self.remaining()
            if self.context_chars + size > self.limits.context_chars:
                raise BudgetExceeded("AGENT_CONTEXT_BUDGET")
            self.context_chars += size

    def record(self, entry, *, update=False):
        with self._lock:
            self.remaining()
            if update:
                self.history[entry["step"] - 1] = deepcopy(entry)
            else:
                self.history.append(deepcopy(entry))

    def snapshot(self):
        with self._lock:
            return {
                "model_call_count": self.requests,
                "tool_call_count": self.tools,
                "context_chars": self.context_chars,
                "history": deepcopy(self.history),
            }

    def pause(self, delay):
        remaining = self.remaining()
        if delay >= remaining:
            raise BudgetExceeded("AGENT_DEADLINE_TIMEOUT")
        if self.cancelled.wait(delay):
            raise BudgetExceeded("AGENT_DEADLINE_TIMEOUT")
        self.remaining()

    @contextmanager
    def scope(self, *, final=False):
        token = ACTIVE_BUDGET.set(self)
        final_token = _FINAL.set(final)
        try:
            self.remaining()
            yield
        finally:
            _FINAL.reset(final_token)
            ACTIVE_BUDGET.reset(token)
