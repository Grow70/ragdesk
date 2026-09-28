"""Native one-shot function calling, with backend validation and offline doubles."""

import json
from copy import deepcopy
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.contracts import ReadArguments, SearchArguments, tool_definitions
from app.llm.contracts import ChatResult, ModelError, ModelUsage
from app.llm.openai import OpenAIChatClient, _usage


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=128)
    name: Literal["search_knowledge", "read_chunks"]
    arguments: dict


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool_calls: list[ToolCall] = Field(max_length=1)


def validate_decision(content) -> Decision:
    try:
        result = Decision.model_validate(content)
        for call in result.tool_calls:
            schema = (
                SearchArguments if call.name == "search_knowledge" else ReadArguments
            )
            call.arguments = schema.model_validate(call.arguments).model_dump()
        return result
    except (ValidationError, TypeError, ValueError):
        raise ModelError("MODEL_INVALID_TOOL_CALL") from None


def provider_tools():
    definitions = tool_definitions()
    result = []
    for definition in definitions:
        schema = definition["parameters"]
        schema["required"] = list(schema["properties"])
        # Provider subset: uniqueness stays enforced by the backend validator.
        if definition["name"] == "read_chunks":
            schema["properties"]["chunk_ids"].pop("uniqueItems", None)
        for field in schema["properties"].values():
            field.pop("default", None)
        result.append(
            {
                "type": "function",
                "function": {
                    "name": definition["name"],
                    "description": definition["description"],
                    "parameters": schema,
                    "strict": True,
                },
            }
        )
    return result


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class DecisionClient(Protocol):
    def decide(self, messages: list[dict]) -> ChatResult: ...


class OpenAIToolDecisionClient(OpenAIChatClient):
    """Reuses existing HTTP timeouts, credentials, usage and bounded retries."""

    def decide(self, messages: list[dict]) -> ChatResult:
        payload, attempts, elapsed = self._post(
            "/chat/completions",
            {
                "model": self.model,
                "messages": messages,
                "tools": provider_tools(),
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "max_completion_tokens": self.max_completion_tokens,
            },
        )
        try:
            choices = payload["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError()
            choice = choices[0]
            message = choice["message"]
            if message.get("refusal"):
                raise ModelError("MODEL_REFUSAL", attempts=attempts)
            calls = message.get("tool_calls", [])
            if calls is None:
                calls = []
            if not isinstance(calls, list) or len(calls) > 1:
                raise ValueError()
            if choice["finish_reason"] != ("tool_calls" if calls else "stop"):
                raise ValueError()
            normalized = []
            for call in calls:
                if call["type"] != "function":
                    raise ValueError()
                raw = call["function"]["arguments"]
                if not isinstance(raw, str) or len(raw) > 24000:
                    raise ValueError()
                normalized.append(
                    {
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "arguments": json.loads(raw, object_pairs_hook=_object),
                    }
                )
            content = validate_decision({"tool_calls": normalized}).model_dump()
        except ModelError as exc:
            raise ModelError(exc.code, attempts=attempts) from None
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
            raise ModelError("MODEL_INVALID_TOOL_CALL", attempts=attempts) from None
        return ChatResult(content, _usage(payload, attempts), elapsed, attempts)


class FakeDecisionClient:
    provider = "fake"
    model = "controlled-tool-decision-v1"

    def __init__(self, content):
        self.content = deepcopy(content)
        self.call_count = 0

    def decide(self, messages: list[dict]) -> ChatResult:
        self.call_count += 1
        # Deliberately unvalidated: the graph must reject malformed fake outputs too.
        return ChatResult(deepcopy(self.content), ModelUsage(), 0.0, 1)
