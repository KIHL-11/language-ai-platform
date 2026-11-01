from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.api import chunk_api, library_api, speaking_api, study_bank_api
from services.auth.identity import (
    AuthenticatedPrincipal,
    DeploymentMode,
    RequestIdentity,
    get_request_identity,
    get_trusted_identity_verifier,
    InvalidAuthentication,
    owner_user_id,
)
from services.retrieval.chunk_memory import ChunkMemoryService
from services.retrieval.chunk_store import ChunkStore
from services.schemas.speaking import SpeakingContext
from services.schemas.study_bank import MediaStudyProvenance, StudyItemInput
from services.speaking.store import SpeakingSessionStore
from services.study_bank.store import StudyBankStore


client = TestClient(app_module.app)


def _principal(user_id: str) -> RequestIdentity:
    return RequestIdentity(
        mode=DeploymentMode.CLOUD,
        principal=AuthenticatedPrincipal(
            user_id=user_id,
            session_id=f"session-{user_id}",
            authentication_method="synthetic-test",
        ),
    )


@contextmanager
def _as_cloud_user(user_id: str):
    app_module.app.dependency_overrides[get_request_identity] = lambda: _principal(
        user_id
    )
    try:
        yield
    finally:
        app_module.app.dependency_overrides.pop(get_request_identity, None)


@pytest.fixture(autouse=True)
def clean_identity_override(monkeypatch):
    app_module.app.dependency_overrides.pop(get_request_identity, None)
    app_module.app.dependency_overrides.pop(get_trusted_identity_verifier, None)
    monkeypatch.delenv("LANGUAGE_AI_DEPLOYMENT_MODE", raising=False)
    if hasattr(app_module.app.state, "identity_verifier"):
        delattr(app_module.app.state, "identity_verifier")
    yield
    app_module.app.dependency_overrides.pop(get_request_identity, None)
    app_module.app.dependency_overrides.pop(get_trusted_identity_verifier, None)


def test_cloud_mode_without_trusted_verifier_fails_closed(monkeypatch):
    monkeypatch.setenv("LANGUAGE_AI_DEPLOYMENT_MODE", "cloud")

    response = client.get("/api/study-bank/items", params={"user_id": "forged"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_identity_not_configured"


def test_arbitrary_user_header_is_not_an_authentication_backdoor(monkeypatch):
    monkeypatch.setenv("LANGUAGE_AI_DEPLOYMENT_MODE", "cloud")

    response = client.get(
        "/api/study-bank/items",
        params={"user_id": "victim"},
        headers={"X-User-Id": "victim"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_identity_not_configured"


def _install_verifier(monkeypatch, verifier):
    monkeypatch.setenv("LANGUAGE_AI_DEPLOYMENT_MODE", "cloud")
    app_module.app.dependency_overrides[get_trusted_identity_verifier] = (
        lambda: verifier
    )


def test_cloud_study_bank_fails_closed_without_postgres_repository(monkeypatch):
    observed = []

    class FakeStudyBank:
        def list_items(self, user_id, **_filters):
            observed.append(user_id)
            return []

    monkeypatch.setattr(study_bank_api, "study_bank_store", FakeStudyBank())
    principal = _principal("owner-a").principal
    _install_verifier(monkeypatch, lambda _request: principal)

    response = client.get(
        "/api/study-bank/items", params={"user_id": "owner-a"}
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_study_bank_storage_not_configured"
    assert observed == []


def test_async_trusted_verifier_is_supported(monkeypatch):
    principal = _principal("owner-a").principal

    async def verifier(_request):
        return principal

    _install_verifier(monkeypatch, verifier)
    response = client.get("/api/chunks/memory", params={"user_id": "owner-a"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("verifier", "status", "code"),
    [
        (lambda _request: None, 401, "authentication_required"),
        (lambda _request: "not-a-principal", 401, "authentication_required"),
        (
            lambda _request: (_ for _ in ()).throw(
                InvalidAuthentication("expired")
            ),
            401,
            "authentication_invalid",
        ),
        (
            lambda _request: (_ for _ in ()).throw(RuntimeError("offline")),
            503,
            "identity_verifier_unavailable",
        ),
    ],
)
def test_trusted_verifier_failures_are_mapped_safely(
    monkeypatch, verifier, status, code
):
    _install_verifier(monkeypatch, verifier)

    response = client.get("/api/chunks/memory", params={"user_id": "owner-a"})

    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


def test_valid_verifier_still_ignores_impersonation_header(monkeypatch):
    principal = _principal("owner-a").principal
    _install_verifier(monkeypatch, lambda _request: principal)

    response = client.get(
        "/api/chunks/memory",
        params={"user_id": "victim"},
        headers={"X-User-Id": "victim"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "identity_mismatch"


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/api/study-bank/items?user_id=victim", None),
        ("PATCH", "/api/study-bank/items/item-1?user_id=victim", {"active": False}),
        ("DELETE", "/api/study-bank/items/item-1?user_id=victim", None),
        (
            "POST",
            "/api/review/items/item-1/rate",
            {"user_id": "victim", "rating": "good", "expected_review_count": 0},
        ),
        ("GET", "/api/chunks/memory?user_id=victim", None),
        (
            "PATCH",
            "/api/chunks/memory/chunk-1",
            {"user_id": "victim", "active": False},
        ),
        ("GET", "/api/speaking/sessions/session-1?user_id=victim", None),
        (
            "POST",
            "/api/speaking/sessions/session-1/events?user_id=victim",
            {
                "expected_event_count": 0,
                "type": "turn",
                "role": "learner",
                "text": "hello",
            },
        ),
        (
            "POST",
            "/api/integration/speaking/sessions/session-1/suggestions/0/save",
            {"user_id": "victim", "target_language": "zh-CN"},
        ),
    ],
)
def test_forged_client_user_id_cannot_read_or_mutate_other_tenant(
    method, path, json
):
    with _as_cloud_user("attacker"):
        response = client.request(method, path, json=json)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "identity_mismatch"


def test_cloud_study_bank_never_falls_back_to_personal_store(monkeypatch):
    observed = []

    class FakeStudyBank:
        def list_items(self, user_id, **_filters):
            observed.append(user_id)
            return []

    monkeypatch.setattr(study_bank_api, "study_bank_store", FakeStudyBank())
    with _as_cloud_user("owner-a"):
        response = client.get(
            "/api/study-bank/items", params={"user_id": "owner-a"}
        )

    assert response.status_code == 503
    assert observed == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/library/items"),
        ("GET", "/api/library/items/another-users-item"),
        ("DELETE", "/api/library/items/another-users-item"),
        ("GET", "/api/library/items/another-users-item/audio"),
        ("GET", "/media/another-users-audio.mp3"),
    ],
)
def test_legacy_library_and_media_fail_closed_in_cloud(method, path):
    with _as_cloud_user("owner-a"):
        response = client.request(method, path)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_tenant_storage_not_configured"


def test_library_cloud_gate_runs_before_legacy_store(monkeypatch):
    class BombStore:
        def list_summaries(self):
            raise AssertionError("legacy unowned store must not be read in cloud mode")

    monkeypatch.setattr(library_api, "library_store", BombStore())
    with _as_cloud_user("owner-a"):
        response = client.get("/api/library/items")

    assert response.status_code == 503


def test_library_update_is_not_reachable_in_cloud_before_owner_migration():
    payload = {
        "is_training": True,
        "stage_index": 0,
        "sentence_index": 0,
        "completed_stages": [],
        "training_order": [
            "active_recall",
            "blind_listening",
            "comprehension",
            "mini_dictation",
            "shadowing",
            "retell",
            "live_dialogue",
        ],
        "lesson_memory_imported": False,
    }
    with _as_cloud_user("owner-a"):
        response = client.patch(
            "/api/library/items/another-users-item/progress", json=payload
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_tenant_storage_not_configured"


def test_cloud_speaking_store_is_fail_closed_before_resource_lookup(monkeypatch):
    class SyntheticSpeakingStore:
        owners = {"session-b": "owner-b"}

        def get(self, user_id, session_id):
            if self.owners.get(session_id) != user_id:
                raise KeyError(session_id)
            raise AssertionError("test must not return another tenant's session")

    monkeypatch.setattr(speaking_api, "speaking_store", SyntheticSpeakingStore())
    with _as_cloud_user("owner-a"):
        response = client.get(
            "/api/speaking/sessions/session-b", params={"user_id": "owner-a"}
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_tenant_storage_not_configured"


@pytest.mark.parametrize(
    "path",
    [
        "/api/realtime/client-secret",
        "/api/speaking/gemini/client-token",
    ],
)
def test_ai_credentials_remain_blocked_until_cloud_budget_controls_exist(path):
    with _as_cloud_user("owner-a"):
        response = client.post(path)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_provider_controls_not_configured"


def test_personal_mode_preserves_legacy_client_identity_contract():
    identity = RequestIdentity(mode=DeploymentMode.PERSONAL)
    assert owner_user_id(identity, "browser-local-learner") == "browser-local-learner"


def test_cloud_mode_rejects_invalid_deployment_mode(monkeypatch):
    monkeypatch.setenv("LANGUAGE_AI_DEPLOYMENT_MODE", "unexpected")

    response = client.get("/api/chunks/memory", params={"user_id": "owner-a"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "deployment_mode_invalid"


def _study_input(user_id: str) -> StudyItemInput:
    return StudyItemInput(
        user_id=user_id,
        kind="word",
        source_language="en",
        target_language="zh-CN",
        text="boundary",
        meaning="边界",
        provenance=[
            MediaStudyProvenance(
                library_item_id="library-owner-b",
                lesson_id="lesson-owner-b",
                source_url="https://example.com/owner-b",
                media_title="Owner B media",
                sentence_id=1,
                sentence_text="Respect the boundary.",
                sentence_translation="尊重边界。",
                start=1,
                end=2,
            )
        ],
    )


def test_study_and_review_resource_ids_are_non_enumerating_and_unchanged(
    tmp_path, monkeypatch
):
    bank = StudyBankStore(
        tmp_path / "study.json",
        clock=lambda: datetime(2026, 10, 28, tzinfo=timezone.utc),
        id_factory=lambda: "study-owner-b",
    )
    item = bank.save_items([_study_input("owner-b")])[0][0]
    monkeypatch.setattr(study_bank_api, "study_bank_store", bank)
    before = bank.path.read_bytes()

    with _as_cloud_user("owner-a"):
        update = client.patch(
            f"/api/study-bank/items/{item.id}",
            params={"user_id": "owner-a"},
            json={"active": False},
        )
        delete = client.delete(
            f"/api/study-bank/items/{item.id}", params={"user_id": "owner-a"}
        )
        rate = client.post(
            f"/api/review/items/{item.id}/rate",
            json={
                "user_id": "owner-a",
                "rating": "good",
                "expected_review_count": 0,
            },
        )

    assert [update.status_code, delete.status_code, rate.status_code] == [503, 503, 503]
    assert bank.path.read_bytes() == before
    preserved = bank.list_items("owner-b", active=None)[0]
    assert preserved.active is True
    assert preserved.review.review_count == 0


def test_chunk_resource_ids_are_non_enumerating_and_unchanged(tmp_path, monkeypatch):
    chunk_store = ChunkStore(tmp_path / "chunks.json")
    memory = ChunkMemoryService(
        chunk_store,
        clock=lambda: datetime(2026, 10, 28, tzinfo=timezone.utc),
        id_factory=lambda: "chunk-owner-b",
    )
    chunk = memory.save_chunk(
        user_id="owner-b",
        text="keep tenants separate",
        meaning="隔离租户",
        language="en",
    )[0]
    monkeypatch.setattr(chunk_api, "chunk_memory_service", memory)
    before = chunk_store.path.read_bytes()

    with _as_cloud_user("owner-a"):
        listed = client.get(
            "/api/chunks/memory", params={"user_id": "owner-a"}
        )
        archive = client.patch(
            f"/api/chunks/memory/{chunk.id}",
            json={"user_id": "owner-a", "active": False},
        )
        practice = client.patch(
            f"/api/chunks/memory/{chunk.id}/practice",
            json={"user_id": "owner-a", "outcome": "success"},
        )

    assert listed.status_code == 200
    assert listed.json() == {"chunks": []}
    assert archive.status_code == practice.status_code == 404
    assert chunk_store.path.read_bytes() == before


def test_real_speaking_store_refuses_other_tenant_resource(tmp_path, monkeypatch):
    sessions = SpeakingSessionStore(
        tmp_path / "speaking.json", id_factory=lambda: "session-owner-b"
    )
    context = SpeakingContext(
        language="en",
        level="B2",
        mode="custom",
        session_goal="Practice",
        target_chunks=[],
        support_words=[],
        system_prompt="Practice safely.",
    )
    session = sessions.create(
        user_id="owner-b",
        provider="gemini-live",
        model="test-model",
        duration=180,
        context=context,
        goal=None,
    )
    monkeypatch.setattr(speaking_api, "speaking_store", sessions)
    before = sessions.path.read_bytes()

    with _as_cloud_user("owner-a"):
        response = client.get(
            f"/api/speaking/sessions/{session.id}",
            params={"user_id": "owner-a"},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_tenant_storage_not_configured"
    assert sessions.path.read_bytes() == before


def _lesson_payload():
    return {
        "id": "lesson-owner-b",
        "title": "Owner B",
        "source_url": "https://example.com/owner-b",
        "source_language": "en",
        "level": "B1",
        "source": "subtitles",
        "sentences": [
            {
                "id": 1,
                "text": "Private sentence.",
                "chunks": [{"text": "private sentence", "meaning": "私密句子"}],
            }
        ],
        "ai": {"provider": "mock", "status": "complete"},
    }


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/chunks/memory",
            {
                "user_id": "owner-a",
                "text": "forged source",
                "language": "en",
                "source_lesson_id": "lesson-owner-b",
                "source_media_id": "library-owner-b",
                "source_stage": "lesson",
            },
        ),
        (
            "/api/chunks/memory/from-lesson",
            {
                "user_id": "owner-a",
                "lesson": _lesson_payload(),
                "source_media_id": "library-owner-b",
            },
        ),
        (
            "/api/study-bank/items/bulk",
            {"items": [_study_input("owner-a").model_dump(mode="json")]},
        ),
    ],
)
def test_unverifiable_cross_resource_provenance_writes_fail_closed(path, payload):
    with _as_cloud_user("owner-a"):
        response = client.post(path, json=payload)

    assert response.status_code == 503
    expected = (
        "cloud_study_bank_storage_not_configured"
        if path == "/api/study-bank/items/bulk"
        else "cloud_tenant_storage_not_configured"
    )
    assert response.json()["detail"]["code"] == expected
