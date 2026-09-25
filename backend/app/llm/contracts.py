"""Provider-independent model result and failure contracts."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    usage: ModelUsage
    elapsed_ms: float
    call_count: int


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: dict
    usage: ModelUsage
    elapsed_ms: float
    call_count: int


class ModelError(Exception):
    """Safe error code; provider response bodies and secrets are never retained."""

    def __init__(self, code: str, *, attempts: int = 0):
        self.code = code
        self.attempts = attempts
        super().__init__(code)


class EmbeddingClient(Protocol):
    call_count: int

    def embed_documents(self, texts: list[str]) -> EmbeddingResult: ...

    def embed_query(self, text: str) -> EmbeddingResult: ...


class ChatClient(Protocol):
    call_count: int

    def generate(self, messages: list[dict], response_schema: dict) -> ChatResult: ...
