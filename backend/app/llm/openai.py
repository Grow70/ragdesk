"""One-provider HTTP adapters for embeddings and structured chat output."""

import json
import math
from collections.abc import Callable
from time import perf_counter
from time import sleep as default_sleep

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.config import Settings
from app.llm.contracts import ChatResult, EmbeddingResult, ModelError, ModelUsage

_API_ROOT = "https://api.openai.com/v1"
_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
_RETRY_NETWORK_ERRORS = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)


def _usage(payload: dict, attempts: int) -> ModelUsage:
    raw = payload.get("usage")
    if not isinstance(raw, dict):
        raise ModelError("MODEL_INVALID_RESPONSE", attempts=attempts)
    input_tokens = raw.get("prompt_tokens")
    output_tokens = raw.get("completion_tokens", 0)
    total_tokens = raw.get("total_tokens")
    if any(
        type(value) is not int or value < 0
        for value in (input_tokens, output_tokens, total_tokens)
    ):
        raise ModelError("MODEL_INVALID_RESPONSE", attempts=attempts)
    return ModelUsage(input_tokens, output_tokens, total_tokens)


class _OpenAIClient:
    def __init__(
        self,
        *,
        api_key: str,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        max_attempts: int = 3,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = default_sleep,
    ):
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is required for real model calls")
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("model timeouts must be positive")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self._api_key = api_key
        self._timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )
        self._http = http_client if http_client is not None else httpx.Client()
        self._owns_http = http_client is None
        self._sleep = sleep
        self.max_attempts = max_attempts
        self.call_count = 0

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _post(self, path: str, body: dict) -> tuple[dict, int, float]:
        start = perf_counter()
        for attempt in range(1, self.max_attempts + 1):
            self.call_count += 1
            try:
                response = self._http.post(
                    f"{_API_ROOT}{path}",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=body,
                    timeout=self._timeout,
                )
            except _RETRY_NETWORK_ERRORS as exc:
                if attempt == self.max_attempts:
                    code = (
                        "MODEL_TIMEOUT"
                        if isinstance(exc, httpx.TimeoutException)
                        else "MODEL_NETWORK_ERROR"
                    )
                    raise ModelError(code, attempts=attempt) from None
                self._sleep(0.2 * 2 ** (attempt - 1))
                continue
            except httpx.RequestError:
                raise ModelError("MODEL_NETWORK_ERROR", attempts=attempt) from None

            if response.status_code in _RETRY_STATUSES:
                if attempt == self.max_attempts:
                    raise ModelError("MODEL_UNAVAILABLE", attempts=attempt)
                self._sleep(0.2 * 2 ** (attempt - 1))
                continue
            if response.status_code in (401, 403):
                raise ModelError("MODEL_AUTH_ERROR", attempts=attempt)
            if response.status_code in (400, 404, 422):
                raise ModelError("MODEL_INVALID_REQUEST", attempts=attempt)
            if response.status_code >= 400:
                raise ModelError("MODEL_HTTP_ERROR", attempts=attempt)
            try:
                payload = response.json()
            except ValueError:
                raise ModelError("MODEL_INVALID_RESPONSE", attempts=attempt) from None
            if not isinstance(payload, dict):
                raise ModelError("MODEL_INVALID_RESPONSE", attempts=attempt)
            return payload, attempt, (perf_counter() - start) * 1000
        raise AssertionError("retry loop did not return or raise")


class OpenAIEmbeddingClient(_OpenAIClient):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        **kwargs,
    ):
        super().__init__(api_key=api_key, **kwargs)
        if not model.strip() or type(dimensions) is not int or dimensions <= 0:
            raise ValueError("embedding model and dimensions must be valid")
        self.model = model
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        if (
            not isinstance(texts, list)
            or not 1 <= len(texts) <= 2048
            or any(not isinstance(text, str) or not text.strip() for text in texts)
        ):
            raise ValueError("texts must contain 1 to 2048 nonempty strings")
        payload, attempts, elapsed_ms = self._post(
            "/embeddings",
            {
                "model": self.model,
                "input": texts,
                "dimensions": self.dimensions,
                "encoding_format": "float",
            },
        )
        data = payload.get("data")
        if payload.get("model") != self.model or not isinstance(data, list):
            raise ModelError("MODEL_INVALID_RESPONSE", attempts=attempts)
        vectors: list[list[float] | None] = [None] * len(texts)
        if len(data) != len(texts):
            raise ModelError("MODEL_INVALID_VECTOR", attempts=attempts)
        for item in data:
            if not isinstance(item, dict):
                raise ModelError("MODEL_INVALID_VECTOR", attempts=attempts)
            index = item.get("index")
            values = item.get("embedding")
            if (
                type(index) is not int
                or not 0 <= index < len(texts)
                or vectors[index] is not None
                or not isinstance(values, list)
                or len(values) != self.dimensions
            ):
                raise ModelError("MODEL_INVALID_VECTOR", attempts=attempts)
            try:
                finite = all(
                    type(value) in (int, float) and math.isfinite(value)
                    for value in values
                )
            except OverflowError:
                finite = False
            if not finite or not any(value != 0 for value in values):
                raise ModelError("MODEL_INVALID_VECTOR", attempts=attempts)
            vectors[index] = [float(value) for value in values]
        if any(vector is None for vector in vectors):
            raise ModelError("MODEL_INVALID_VECTOR", attempts=attempts)
        return EmbeddingResult(
            vectors=[vector for vector in vectors if vector is not None],
            usage=_usage(payload, attempts),
            elapsed_ms=elapsed_ms,
            call_count=attempts,
        )

    def embed_query(self, text: str) -> EmbeddingResult:
        return self.embed_documents([text])


class OpenAIChatClient(_OpenAIClient):
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4.1-mini-2025-04-14",
        **kwargs,
    ):
        super().__init__(api_key=api_key, **kwargs)
        if not model.strip():
            raise ValueError("chat model must not be blank")
        self.model = model

    def generate(self, messages: list[dict], response_schema: dict) -> ChatResult:
        if not messages or any(
            not isinstance(message, dict)
            or message.get("role") not in ("system", "developer", "user", "assistant")
            or not isinstance(message.get("content"), str)
            or not message["content"].strip()
            for message in messages
        ):
            raise ValueError("messages must be nonempty text messages")
        if (
            not isinstance(response_schema, dict)
            or response_schema.get("type") != "object"
        ):
            raise ValueError("response_schema must be a JSON object schema")
        try:
            Draft202012Validator.check_schema(response_schema)
        except SchemaError:
            raise ValueError("response_schema is invalid") from None
        payload, attempts, elapsed_ms = self._post(
            "/chat/completions",
            {
                "model": self.model,
                "messages": messages,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "ragdesk_response",
                        "strict": True,
                        "schema": response_schema,
                    },
                },
            },
        )
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ModelError("MODEL_INVALID_RESPONSE", attempts=attempts)
        choice = choices[0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ModelError("MODEL_INVALID_RESPONSE", attempts=attempts)
        message = choice["message"]
        if message.get("refusal"):
            raise ModelError("MODEL_REFUSAL", attempts=attempts)
        if choice.get("finish_reason") != "stop" or not isinstance(
            message.get("content"), str
        ):
            raise ModelError("MODEL_INVALID_RESPONSE", attempts=attempts)
        try:
            content = json.loads(message["content"])
        except ValueError:
            raise ModelError("MODEL_INVALID_RESPONSE", attempts=attempts) from None
        if not isinstance(content, dict):
            raise ModelError("MODEL_INVALID_RESPONSE", attempts=attempts)
        try:
            Draft202012Validator(response_schema).validate(content)
        except ValidationError:
            raise ModelError("MODEL_INVALID_RESPONSE", attempts=attempts) from None
        return ChatResult(content, _usage(payload, attempts), elapsed_ms, attempts)


def create_openai_clients(
    settings: Settings,
) -> tuple[OpenAIEmbeddingClient, OpenAIChatClient]:
    """Fail explicitly without a key; never replace a real provider with fakes."""

    if settings.openai_api_key is None:
        raise ValueError("OPENAI_API_KEY is required for real model calls")
    key = settings.openai_api_key.get_secret_value()
    options = {
        "api_key": key,
        "connect_timeout": settings.model_connect_timeout_seconds,
        "read_timeout": settings.model_read_timeout_seconds,
        "max_attempts": settings.model_max_attempts,
    }
    return (
        OpenAIEmbeddingClient(
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            **options,
        ),
        OpenAIChatClient(model=settings.chat_model, **options),
    )
