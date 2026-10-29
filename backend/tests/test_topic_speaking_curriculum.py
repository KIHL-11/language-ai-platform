from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.api import speaking_api
from services.auth.identity import (
    AuthenticatedPrincipal, DeploymentMode, RequestIdentity, get_request_identity,
)
from services.schemas.study_bank import AiTextStudyProvenance, StudyItemInput, StudyItemPatch
from services.schemas.topics import SpeakingTopic
from services.speaking.store import SpeakingSessionStore
from services.study_bank.store import StudyBankStore
from services.topics.catalog import CATALOG_VERSION, TOPICS, get_topic


NOW = datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc)
client = TestClient(app_module.app)


def value(user="learner", text="let's catch up", language="en"):
    return StudyItemInput(
        user_id=user, kind="chunk", source_language=language,
        target_language="zh-CN", text=text, meaning="meaning", provenance=[
            AiTextStudyProvenance(
                source_text=f"Practice {text} today.", evidence_start=9,
                evidence_end=9 + len(text),
            )
        ],
    )


@pytest.fixture
def personal(tmp_path, monkeypatch):
    bank = StudyBankStore(tmp_path / "study.json", clock=lambda: NOW)
    sessions = SpeakingSessionStore(tmp_path / "speaking.json", clock=lambda: NOW)
    monkeypatch.setattr(speaking_api, "study_bank", bank)
    monkeypatch.setattr(speaking_api, "speaking_store", sessions)
    return bank, sessions


@pytest.fixture(autouse=True)
def identity_cleanup(monkeypatch):
    app_module.app.dependency_overrides.pop(get_request_identity, None)
    monkeypatch.delenv("LANGUAGE_AI_DEPLOYMENT_MODE", raising=False)
    previous = getattr(app_module.app.state, "cloud_study_bank_repository", None)
    yield
    app_module.app.dependency_overrides.pop(get_request_identity, None)
    if previous is None and hasattr(app_module.app.state, "cloud_study_bank_repository"):
        delattr(app_module.app.state, "cloud_study_bank_repository")
    elif previous is not None:
        app_module.app.state.cloud_study_bank_repository = previous


def test_catalog_is_versioned_bounded_and_covers_both_languages_and_levels():
    response = client.get("/api/topics")
    assert response.status_code == 200
    payload = response.json()
    assert payload["catalog_version"] == CATALOG_VERSION == 1
    assert len(payload["topics"]) == 14
    assert {(item["language"], item["level"]) for item in payload["topics"]} == {
        (language, level) for language in ("en", "de") for level in ("B1", "B2", "C1")
    }
    assert {item["topic_id"].split("-", 1)[1] for item in payload["topics"]} == {
        "friends-reunion", "shopping-return", "airport-hotel", "shared-housing",
        "job-interview", "workplace-meeting", "university-discussion",
    }
    assert all("keywords" not in item for item in payload["topics"])


def test_catalog_filters_and_invalid_values_or_topic_are_controlled():
    response = client.get("/api/topics", params={"language": "de", "level": "C1"})
    assert response.status_code == 200
    assert [item["topic_id"] for item in response.json()["topics"]] == [
        "de-workplace-meeting", "de-university-discussion"
    ]
    assert client.get("/api/topics", params={"language": "fr"}).status_code == 422
    assert client.get("/api/topics/en-does-not-exist").status_code == 404


def test_personal_preview_selects_only_active_language_chunks_and_preserves_srs(personal):
    bank, _ = personal
    active = bank.save_items([value(text="let's catch up")])[0][0]
    archived = bank.save_items([value(text="next time")])[0][0]
    bank.update_item("learner", archived.id, StudyItemPatch(active=False))
    bank.save_items([value(text="Lass uns treffen", language="de")])
    before = bank.path.read_bytes()
    response = client.post("/api/topics/en-friends-reunion/preview", json={"user_id": "learner", "target_limit": 5})
    assert response.status_code == 200
    assert [item["study_item_id"] for item in response.json()["target_chunks"]] == [active.id]
    assert bank.path.read_bytes() == before
    assert "not a fluency" in response.json()["evidence_notice"]


def test_personal_attempt_is_explicit_mock_with_final_evidence_and_no_network(personal, monkeypatch):
    bank, sessions = personal
    target = bank.save_items([value(text="let's catch up")])[0][0]
    before = bank.path.read_bytes()
    monkeypatch.setattr(speaking_api.requests, "post", lambda *args, **kwargs: pytest.fail("network called"))
    started = client.post("/api/topics/en-friends-reunion/attempts", json={"user_id": "learner", "target_ids": [target.id]})
    assert started.status_code == 200
    state = started.json()
    stored = sessions.get("learner", state["session_id"])
    assert stored.provider == "topic-mock" and stored.model == "deterministic-topic-mock-v1" and stored.turns == []
    turn = client.post(
        f"/api/topics/en-friends-reunion/attempts/{state['session_id']}/turns",
        json={"user_id": "learner", "expected_event_count": 0, "text": "Let's catch up next time."},
    )
    assert turn.status_code == 200
    assert turn.json()["state"]["target_usage"][0]["use_count"] == 1
    assert turn.json()["state"]["target_usage"][0]["evidence"] == ["Let's catch up next time."]
    finished = client.post(
        f"/api/topics/en-friends-reunion/attempts/{state['session_id']}/finish",
        json={"user_id": "learner", "expected_event_count": 2, "interrupted": False},
    )
    assert finished.status_code == 200
    feedback = finished.json()["feedback"]
    assert feedback["completion"] == "completed"
    assert any(item["observed"] and item["evidence"] == "Let's catch up next time." for item in feedback["checkpoints"])
    assert "fluency" in " ".join(feedback["limitations"])
    assert bank.path.read_bytes() == before


@pytest.mark.parametrize(
    "topic_id,text,expected",
    [
        ("en-workplace-meeting", "I am agree because the risk is high.", "I agree because the risk is high."),
        ("de-shared-housing", "Ich spreche mit das Team über den Lärm.", "Ich spreche mit dem Team über den Lärm."),
    ],
)
def test_feedback_correction_is_grounded_in_actual_final_turn(personal, topic_id, text, expected):
    started = client.post(f"/api/topics/{topic_id}/attempts", json={"user_id": "learner", "target_ids": []}).json()
    turned = client.post(
        f"/api/topics/{topic_id}/attempts/{started['session_id']}/turns",
        json={"user_id": "learner", "expected_event_count": 0, "text": text},
    ).json()
    finished = client.post(
        f"/api/topics/{topic_id}/attempts/{started['session_id']}/finish",
        json={"user_id": "learner", "expected_event_count": turned["state"]["event_count"], "interrupted": False},
    ).json()
    assert finished["feedback"]["corrections"][0]["learner_text"] == text
    assert finished["feedback"]["corrections"][0]["corrected_text"] == expected


def test_interruption_and_stale_or_foreign_attempts_are_safe(personal):
    started = client.post("/api/topics/de-airport-hotel/attempts", json={"user_id": "owner-a", "target_ids": []}).json()
    path = f"/api/topics/de-airport-hotel/attempts/{started['session_id']}"
    assert client.post(f"{path}/turns", json={"user_id": "owner-b", "expected_event_count": 0, "text": "Hallo"}).status_code == 404
    interrupted = client.post(f"{path}/finish", json={"user_id": "owner-a", "expected_event_count": 0, "interrupted": True})
    assert interrupted.status_code == 200
    assert interrupted.json()["feedback"]["completion"] == "interrupted"
    assert client.post(f"{path}/finish", json={"user_id": "owner-a", "expected_event_count": 0, "interrupted": True}).status_code == 409


def test_inaccessible_or_archived_target_is_non_enumerating(personal):
    bank, _ = personal
    archived = bank.save_items([value(text="next time")])[0][0]
    bank.update_item("learner", archived.id, StudyItemPatch(active=False))
    for item_id in (archived.id, "someone-elses-id"):
        response = client.post("/api/topics/en-friends-reunion/attempts", json={"user_id": "learner", "target_ids": [item_id]})
        assert response.status_code == 404 and response.json()["detail"] == "Topic resource not found"


class FakeCloudStudy:
    def __init__(self, values): self.values = values
    def assert_readable(self): return None
    def list_items(self, owner_user_id, **filters):
        return [item for item in self.values if item.user_id == owner_user_id and item.active and item.kind == filters.get("kind") and item.source_language == filters.get("language")]
    def get_item(self, *args): raise KeyError
    def save_items_for_owner(self, *args): raise AssertionError("write")
    def update_item(self, *args): raise AssertionError("write")
    def delete_item(self, *args): raise AssertionError("write")
    def rate_item(self, *args): raise AssertionError("write")
    def active_chunk_keys(self, *args): return set()


@contextmanager
def cloud(owner, repository):
    app_module.app.dependency_overrides[get_request_identity] = lambda: RequestIdentity(
        mode=DeploymentMode.CLOUD,
        principal=AuthenticatedPrincipal(user_id=owner, session_id=f"session-{owner}", authentication_method="test"),
    )
    app_module.app.state.cloud_study_bank_repository = repository
    try: yield
    finally: app_module.app.dependency_overrides.pop(get_request_identity, None)


def test_cloud_preview_uses_principal_and_does_not_leak_other_owner(personal):
    bank, _ = personal
    a = bank.save_items([value(user="owner-a", text="let's catch up")])[0][0]
    bank.save_items([value(user="owner-b", text="next time")])
    repository = FakeCloudStudy(bank.list_items("owner-a", active=None) + bank.list_items("owner-b", active=None))
    with cloud("owner-a", repository):
        own = client.post("/api/topics/en-friends-reunion/preview", json={"user_id": "owner-a", "target_limit": 5})
        forged = client.post("/api/topics/en-friends-reunion/preview", json={"user_id": "owner-b", "target_limit": 5})
    assert [item["study_item_id"] for item in own.json()["target_chunks"]] == [a.id]
    assert forged.status_code == 403


def test_cloud_attempt_fails_before_legacy_speaking_or_provider_access(personal, monkeypatch):
    bank, _ = personal
    repository = FakeCloudStudy(bank.list_items("learner", active=None))
    monkeypatch.setattr(speaking_api.speaking_store, "create", lambda **kwargs: pytest.fail("legacy store touched"))
    monkeypatch.setattr(speaking_api.requests, "post", lambda *args, **kwargs: pytest.fail("provider called"))
    with cloud("owner-a", repository):
        response = client.post("/api/topics/en-friends-reunion/attempts", json={"user_id": "owner-a", "target_ids": []})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_tenant_storage_not_configured"


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("POST", "/api/speaking/context", {"user_id": "owner-a", "language": "en", "level": "B1", "mode": "review_weak"}),
        ("POST", "/api/speaking/sessions", {"user_id": "owner-a", "language": "en", "level": "B1", "mode": "custom", "provider": "gemini-live", "duration_seconds": 180}),
        ("GET", "/api/speaking/sessions?user_id=owner-a", None),
        ("GET", "/api/speaking/sessions/session-x?user_id=owner-a", None),
        ("POST", "/api/speaking/sessions/session-x/events?user_id=owner-a", {"expected_event_count": 0, "type": "turn", "role": "learner", "text": "hello"}),
        ("POST", "/api/speaking/sessions/session-x/finish?user_id=owner-a", {"expected_event_count": 0, "status": "interrupted", "end_reason": "user"}),
        ("POST", "/api/speaking/sessions/session-x/tools", {"user_id": "owner-a", "expected_event_count": 0, "name": "get_due_chunks", "arguments": {"language": "en", "limit": 5}}),
    ],
)
def test_all_cloud_speaking_json_routes_fail_closed(personal, monkeypatch, method, path, payload):
    class ForbiddenStore:
        def __getattr__(self, name): return lambda *args, **kwargs: pytest.fail(f"legacy store touched: {name}")
    monkeypatch.setattr(speaking_api, "speaking_store", ForbiddenStore())
    with cloud("owner-a", FakeCloudStudy([])):
        response = client.request(method, path, json=payload)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_tenant_storage_not_configured"


def test_catalog_models_are_immutable_source_data_and_well_formed():
    assert len({topic.topic_id for topic in TOPICS}) == len(TOPICS)
    for topic in TOPICS:
        assert get_topic(topic.topic_id) == topic
        assert 2 <= len(topic.success_criteria) <= 5
        assert 2 <= len(topic.guided_prompts) <= 6


@pytest.mark.parametrize("change", [{"version": 0}, {"language": "fr"}, {"state": "draft"}])
def test_malformed_catalog_topic_or_version_is_rejected(change):
    payload = TOPICS[0].model_dump()
    payload.update(change)
    with pytest.raises(ValueError):
        SpeakingTopic.model_validate(payload)
