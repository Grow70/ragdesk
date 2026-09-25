"""Deterministic offline doubles; never used as a live-call fallback."""

from copy import deepcopy
from hashlib import sha256
from time import perf_counter

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from app.llm.contracts import ChatResult, EmbeddingResult, ModelError, ModelUsage


class FakeEmbeddingClient:
    def __init__(self, *, dimensions: int = 1536):
        if type(dimensions) is not int or dimensions <= 0:
            raise ValueError("dimensions must be a positive integer")
        self.dimensions = dimensions
        self.provider = "fake"
        self.model = "sha256-onehot-v1"
        self.call_count = 0

    def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        if not texts or any(
            not isinstance(text, str) or not text.strip() for text in texts
        ):
            raise ValueError("texts must be nonempty strings")
        start = perf_counter()
        self.call_count += 1
        vectors = []
        for text in texts:
            digest = sha256(text.encode("utf-8")).digest()
            vector = [0.0] * self.dimensions
            vector[int.from_bytes(digest[:4], "big") % self.dimensions] = 1.0
            vectors.append(vector)
        return EmbeddingResult(
            vectors, ModelUsage(), (perf_counter() - start) * 1000, 1
        )

    def embed_query(self, text: str) -> EmbeddingResult:
        return self.embed_documents([text])


class FakeChatClient:
    def __init__(self, output: dict):
        self.output = deepcopy(output)
        self.call_count = 0

    def generate(self, messages: list[dict], response_schema: dict) -> ChatResult:
        if not messages:
            raise ValueError("messages must not be empty")
        Draft202012Validator.check_schema(response_schema)
        start = perf_counter()
        self.call_count += 1
        content = deepcopy(self.output)
        try:
            Draft202012Validator(response_schema).validate(content)
        except ValidationError:
            raise ModelError("MODEL_INVALID_RESPONSE", attempts=1) from None
        return ChatResult(content, ModelUsage(), (perf_counter() - start) * 1000, 1)
