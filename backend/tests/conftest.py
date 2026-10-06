import pytest


@pytest.fixture(autouse=True)
def isolate_development_ai_mode(monkeypatch):
    monkeypatch.delenv("AI_MODE", raising=False)
