"""Offline model adapter contract and failure policy checks."""

import json
import math

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.llm.contracts import ModelError
from app.llm.fake import FakeChatClient, FakeEmbeddingClient
from app.llm.openai import (
    OpenAIChatClient,
    OpenAIEmbeddingClient,
    create_openai_clients,
)

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _embedding_response(vectors, *, usage=None):
    return {
        "model": "text-embedding-3-small",
        "data": [
            {"index": index, "embedding": vector}
            for index, vector in enumerate(vectors)
        ],
        "usage": usage or {"prompt_tokens": 4, "total_tokens": 4},
    }


def _chat_response(content='{"answer":"可以"}'):
    return {
        "choices": [
            {"finish_reason": "stop", "message": {"content": content, "refusal": None}}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }


def test_fake_clients_are_deterministic_and_expose_usage():
    embedding = FakeEmbeddingClient(dimensions=8)
    first = embedding.embed_documents(["报销", "休假"])
    assert first.vectors == embedding.embed_documents(["报销", "休假"]).vectors
    assert len(first.vectors) == 2
    assert len(first.vectors[0]) == 8
    assert embedding.embed_query("报销").vectors == first.vectors[:1]
    assert embedding.call_count == 3
    assert first.usage.total_tokens == 0
    chat = FakeChatClient({"answer": "演示"})
    answer = chat.generate([{"role": "user", "content": "你好"}], SCHEMA)
    assert answer.content == {"answer": "演示"}
    assert answer.call_count == 1
    assert chat.call_count == 1


def test_embedding_order_usage_and_requested_dimensions():
    def handler(request):
        assert request.url.path == "/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.extensions["timeout"]["connect"] == 5.0
        assert request.extensions["timeout"]["read"] == 30.0
        assert request.read().decode().count("文档") == 2
        body = _embedding_response([[1.0, 0.0], [0.0, 2.0]])
        body["data"].reverse()
        return httpx.Response(200, json=body)

    client = OpenAIEmbeddingClient(
        api_key="test-key",
        dimensions=2,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.embed_documents(["文档甲", "文档乙"])
    assert result.vectors == [[1.0, 0.0], [0.0, 2.0]]
    assert (result.usage.input_tokens, result.call_count, client.call_count) == (
        4,
        1,
        1,
    )
    assert result.elapsed_ms >= 0


@pytest.mark.parametrize(
    "vectors",
    [
        [],
        [[1.0]],
        [[0.0, 0.0]],
        [[math.nan, 1.0]],
        [[math.inf, 1.0]],
        [[True, 1.0]],
        [[10**400, 1.0]],
    ],
)
def test_bad_embedding_output_is_not_retried(vectors):
    client = OpenAIEmbeddingClient(
        api_key="test-key",
        dimensions=2,
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    content=json.dumps(_embedding_response(vectors)),
                    headers={"Content-Type": "application/json"},
                )
            )
        ),
    )
    with pytest.raises(ModelError, match="MODEL_INVALID_VECTOR"):
        client.embed_query("测试")
    assert client.call_count == 1


@pytest.mark.parametrize(
    "status,code", [(401, "MODEL_AUTH_ERROR"), (400, "MODEL_INVALID_REQUEST")]
)
def test_auth_and_bad_parameter_are_not_retried(status, code):
    client = OpenAIEmbeddingClient(
        api_key="secret-sentinel",
        dimensions=2,
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(status, text="secret-sentinel")
            )
        ),
    )
    with pytest.raises(ModelError, match=code) as error:
        client.embed_query("测试")
    assert error.value.attempts == 1
    assert client.call_count == 1
    assert "secret-sentinel" not in str(error.value)


def test_only_transient_errors_retry_up_to_three_attempts():
    statuses = iter([429, 503, 200])

    def handler(_request):
        status = next(statuses)
        return httpx.Response(
            status,
            json=_embedding_response([[1.0, 0.0]]) if status == 200 else {},
        )

    client = OpenAIEmbeddingClient(
        api_key="test-key",
        dimensions=2,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )
    result = client.embed_query("测试")
    assert result.call_count == 3
    assert client.call_count == 3


def test_timeout_is_limited_and_no_fake_fallback():
    def timeout(_request):
        raise httpx.ReadTimeout("timeout")

    client = OpenAIEmbeddingClient(
        api_key="test-key",
        dimensions=2,
        http_client=httpx.Client(transport=httpx.MockTransport(timeout)),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(ModelError, match="MODEL_TIMEOUT") as error:
        client.embed_query("测试")
    assert error.value.attempts == 3
    assert client.call_count == 3


def test_chat_sends_schema_and_validates_output():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert b'"json_schema"' in request.read()
        return httpx.Response(200, json=_chat_response())

    client = OpenAIChatClient(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.generate([{"role": "user", "content": "你好"}], SCHEMA)
    assert result.content == {"answer": "可以"}
    assert result.usage.total_tokens == 7
    assert result.call_count == 1


def test_chat_schema_violation_and_bad_input_do_not_retry():
    client = OpenAIChatClient(
        api_key="test-key",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200, json=_chat_response('{"other":"x"}')
                )
            )
        ),
    )
    with pytest.raises(ModelError, match="MODEL_INVALID_RESPONSE"):
        client.generate([{"role": "user", "content": "你好"}], SCHEMA)
    assert client.call_count == 1
    with pytest.raises(ValueError):
        client.generate([], SCHEMA)
    assert client.call_count == 1


def test_real_client_factory_needs_key_and_keeps_models_separate():
    settings = Settings(
        database_url="postgresql+psycopg://localhost/demo",
        jwt_secret="x" * 32,
        openai_api_key=None,
    )
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_openai_clients(settings)
    configured = settings.model_copy(
        update={"openai_api_key": SecretStr("test-key"), "embedding_dimensions": 512}
    )
    embedding, chat = create_openai_clients(configured)
    assert embedding.model == "text-embedding-3-small"
    assert embedding.dimensions == 512
    assert chat.model == "gpt-4.1-mini-2025-04-14"
    embedding.close()
    chat.close()
