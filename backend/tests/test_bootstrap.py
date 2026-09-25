import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app


@pytest.fixture
def configured_env(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ragdesk:db-secret-sentinel@localhost/ragdesk",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "model-secret-sentinel")
    monkeypatch.setenv("JWT_SECRET", "jwt-secret-sentinel-with-at-least-32-bytes")


def test_health_returns_request_id(configured_env):
    with TestClient(create_app()) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_missing_required_configuration_is_explicit(monkeypatch):
    for name in ("DATABASE_URL", "JWT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL.*JWT_SECRET"):
        load_settings()


def test_errors_use_envelope_without_leaking_secrets(configured_env, capfd):
    app = create_app()

    @app.get("/test/fail")
    def fail():
        raise RuntimeError("model-secret-sentinel")

    @app.get("/test/number/{item_id}")
    def number(item_id: int, request: Request):
        return {"item_id": item_id, "request_id": request.state.request_id}

    with TestClient(app, raise_server_exceptions=False) as client:
        missing = client.get("/missing")
        invalid = client.get("/test/number/not-a-number")
        failed = client.get("/test/fail")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "HTTP_ERROR"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_REQUEST"
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "INTERNAL_ERROR"
    for response in (missing, invalid, failed):
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert "db-secret-sentinel" not in output
    assert "model-secret-sentinel" not in output
    assert "jwt-secret-sentinel" not in output
    assert "model-secret-sentinel" not in str(failed.json())
