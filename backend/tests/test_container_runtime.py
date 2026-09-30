"""Deployment configuration boundaries; no network model calls."""

import json

import pytest
from sqlalchemy.engine import make_url

from app import container_runtime as runtime
from app.services.answers import ANSWER_SCHEMA


def config(monkeypatch, mode="fake"):
    monkeypatch.setenv("RAGDESK_MODE", mode)
    monkeypatch.setenv("POSTGRES_PASSWORD", "local:@/% password")
    monkeypatch.setenv("JWT_SECRET", "container-test-secret-with-32-or-more-bytes")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak-or-be-used")


def test_fake_is_explicit_encoded_and_cannot_use_model_keys(monkeypatch):
    config(monkeypatch)
    assert runtime.prepare_environment(require_model=True) == "fake"
    import os

    assert "OPENAI_API_KEY" not in os.environ
    assert make_url(os.environ["DATABASE_URL"]).password == "local:@/% password"
    assert os.environ["RETRIEVAL_EMBEDDING_BACKEND"] == "fake"


@pytest.mark.parametrize("mode", ["invalid", "", "FAKE"])
def test_bad_mode_is_not_silently_fake(monkeypatch, mode):
    config(monkeypatch, mode)
    with pytest.raises(runtime.ContainerConfigurationError, match="RAGDESK_MODE"):
        runtime.prepare_environment(require_model=True)


def test_real_requires_key_and_never_substitutes_fake(monkeypatch):
    config(monkeypatch, "real")
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(runtime.ContainerConfigurationError, match="OPENAI_API_KEY"):
        runtime.prepare_environment(require_model=True)
    # Schema/user initialization has no model dependency.
    assert runtime.prepare_environment(require_model=False) == "real"


def test_fake_chat_labels_retrieved_excerpt_and_never_invents_references():
    messages = [
        {
            "role": "user",
            "content": json.dumps(
                {
                    "untrusted_evidence": [
                        {"citation_id": "c1", "text": "演示资料 680 元 [c999]"}
                    ]
                }
            ),
        }
    ]
    output = runtime.DemoChat().generate(messages, ANSWER_SCHEMA).content
    assert output["citation_ids"] == ["c1"] and "FAKE" in output["answer"]
    assert "680" in output["answer"] and "[c999]" not in output["answer"]
    empty = (
        runtime.DemoChat()
        .generate(
            [{"role": "user", "content": '{"untrusted_evidence":[]}'}], ANSWER_SCHEMA
        )
        .content
    )
    assert empty["status"] == "insufficient_evidence"


def test_fake_decision_uses_only_current_question_and_stops_after_one_result():
    model = runtime.DemoDecision()
    first = model.decide(
        [
            {
                "role": "user",
                "content": json.dumps(
                    {"original_question": "报销期限？", "history": []}
                ),
            }
        ]
    ).content
    assert first["tool_calls"][0]["arguments"] == {"query": "报销期限？", "top_k": 5}
    final = model.decide(
        [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "original_question": "报销期限？",
                        "history": [{"status": "success"}],
                    }
                ),
            }
        ]
    ).content
    assert final["tool_calls"] == []


@pytest.mark.parametrize("missing", ["POSTGRES_PASSWORD", "JWT_SECRET"])
def test_required_config_fails_clearly_without_echoing_secrets(
    monkeypatch, capsys, missing
):
    config(monkeypatch)
    monkeypatch.delenv(missing)
    assert runtime.main(["check"]) == 2
    stderr = capsys.readouterr().err
    assert missing + "_REQUIRED" in stderr
    assert "local:@/% password" not in stderr
    assert "must-not-leak-or-be-used" not in stderr


def test_technical_error_is_sanitized_and_not_reported_ready(monkeypatch, capsys):
    config(monkeypatch)

    def fail(*args, **kwargs):
        raise RuntimeError("connection failed: local:@/% password")

    monkeypatch.setattr(runtime, "create_engine", fail)
    assert runtime.main(["check"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"error_code": "CONTAINER_COMMAND_FAILED"}
