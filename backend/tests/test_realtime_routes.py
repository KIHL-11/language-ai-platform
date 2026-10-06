from unittest.mock import Mock

import app as app_module
from fastapi.testclient import TestClient

from services.ai.llm_client import llm
from services.api import realtime_api


client = TestClient(app_module.app)


def upstream_response(status_code=200, payload=None, reason="OK"):
    response = Mock()
    response.ok = 200 <= status_code < 300
    response.status_code = status_code
    response.reason = reason
    response.json.return_value = payload or {}
    return response


def test_client_secret_requires_server_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    post = Mock()
    monkeypatch.setattr(realtime_api.requests, "post", post)

    response = client.post("/api/realtime/client-secret")

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]
    post.assert_not_called()


def test_client_secret_returns_only_temporary_credential(monkeypatch):
    server_key = "sk-server-secret-must-not-leak"
    monkeypatch.setenv("OPENAI_API_KEY", server_key)
    monkeypatch.setenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1-mini")
    monkeypatch.setenv("OPENAI_REALTIME_VOICE", "marin")
    post = Mock(return_value=upstream_response(payload={
        "value": "ek_short_lived",
        "expires_at": 123456,
        "ignored": server_key,
    }))
    monkeypatch.setattr(realtime_api.requests, "post", post)

    response = client.post("/api/realtime/client-secret")

    assert response.status_code == 200
    assert response.json() == {
        "value": "ek_short_lived",
        "expires_at": 123456,
        "model": "gpt-realtime-2.1-mini",
        "voice": "marin",
    }
    assert server_key not in response.text
    request = post.call_args
    assert request.args[0] == "https://api.openai.com/v1/realtime/client_secrets"
    assert request.kwargs["headers"]["Authorization"] == f"Bearer {server_key}"
    assert "OpenAI-Beta" not in request.kwargs["headers"]
    assert request.kwargs["json"]["session"]["type"] == "realtime"


def test_client_secret_handles_upstream_failure_without_leaking_key(monkeypatch):
    server_key = "sk-private"
    monkeypatch.setenv("OPENAI_API_KEY", server_key)
    monkeypatch.setattr(
        realtime_api.requests,
        "post",
        Mock(return_value=upstream_response(
            status_code=401,
            payload={"error": {"message": f"Invalid key {server_key}"}},
            reason="Unauthorized",
        )),
    )

    response = client.post("/api/realtime/client-secret")

    assert response.status_code == 502
    assert "401" in response.json()["detail"]
    assert server_key not in response.text


def test_lesson_api_does_not_create_realtime_credentials(monkeypatch):
    realtime_post = Mock()
    monkeypatch.setattr(realtime_api.requests, "post", realtime_post)
    monkeypatch.setattr(llm, "chat", lambda messages: "[]")

    response = client.post(
        "/api/lesson/create",
        json={"title": "Local lesson", "sentences": ["Hello world."]},
    )

    assert response.status_code == 200
    realtime_post.assert_not_called()
