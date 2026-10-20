import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.api import speaking_api
from services.library.store import LearningLibraryStore
from services.schemas.lesson import Lesson
from services.schemas.speaking import (
    SpeakingContextRequest, SpeakingCorrection, SpeakingEventRequest,
    SpeakingFinishRequest, SpeakingSuggestion,
)
from services.schemas.study_bank import ReviewState, StudyItemInput, StudyProvenance
from services.speaking.context import SpeakingContextBuilder
from services.speaking.matching import contains_phrase
from services.speaking.store import SpeakingConflictError, SpeakingSessionStore, SpeakingStoreError
from services.speaking.tools import ToolDispatcher, ToolError
from services.study_bank.store import StudyBankStore


NOW = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
client = TestClient(app_module.app)


def provenance(library_item_id="library-one", sentence_id=1):
    return StudyProvenance(
        library_item_id=library_item_id, lesson_id="lesson-one",
        source_url="https://youtu.be/speaking", media_title="Speaking media",
        sentence_id=sentence_id, sentence_text="As far as I know, it depends on the plan.",
        sentence_translation="据我所知，这取决于计划。", start=1, end=4,
    )


def study(kind="chunk", text="as far as I know", *, library_item_id="library-one", language="en"):
    return StudyItemInput(
        user_id="learner", kind=kind, source_language=language, target_language="zh-CN",
        text=text, meaning="meaning", provenance=[provenance(library_item_id)],
    )


def lesson(language="en"):
    sentences = [{"id": index, "text": f"Sentence {index}", "translation": f"句子 {index}", "start": index, "end": index + 1, "ai_status": "ok"} for index in range(1, 16)]
    return Lesson.model_validate({
        "id": "lesson-one", "title": "Speaking media", "source_url": "https://youtu.be/speaking",
        "source_language": language, "target_language": "zh-CN", "level": "B2", "source": "subtitles",
        "sentences": sentences, "ai": {"provider": "mock", "status": "complete"},
    })


def stores(tmp_path):
    bank = StudyBankStore(tmp_path / "study.json", clock=lambda: NOW, id_factory=(lambda values=iter(f"study-{i}" for i in range(20)): next(values)))
    library = LearningLibraryStore(tmp_path / "library.json", tmp_path / "library_media", tmp_path / "media", lambda: NOW)
    sessions = SpeakingSessionStore(tmp_path / "speaking.json", clock=lambda: NOW, id_factory=lambda: "session-one")
    return bank, library, sessions


def request(**updates):
    value = {"user_id": "learner", "language": "en", "level": "B2", "mode": "review_weak", "target_limit": 5}
    value.update(updates)
    return SpeakingContextRequest(**value)


def set_review(bank, item_id, **changes):
    payload = json.loads(bank.path.read_text())
    record = next(value for value in payload["items"] if value["id"] == item_id)
    review = record["review"]
    review.update(changes)
    if changes.get("last_rating"):
        rating = changes["last_rating"]
        review.update({"review_count": 1, "last_reviewed_at": NOW.isoformat(), "again_count": int(rating == "again"), "hard_count": int(rating == "hard"), "good_count": int(rating == "good"), "easy_count": int(rating == "easy")})
    bank.path.write_text(json.dumps(payload), encoding="utf-8")


def test_provider_status_is_secret_free(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    response = client.get("/api/speaking/providers")
    assert response.status_code == 200
    assert [item["configured"] for item in response.json()["providers"]] == [True, True]
    assert "secret" not in response.text
    monkeypatch.delenv("GEMINI_API_KEY")
    assert client.get("/api/speaking/providers").json()["providers"][0]["configured"] is False


def test_gemini_token_missing_config_and_mocked_success(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert client.post("/api/speaking/gemini/client-token").status_code == 503
    monkeypatch.setenv("GEMINI_API_KEY", "private-key")
    captured = {}

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return SimpleNamespace(ok=True, json=lambda: {"name": "ephemeral-token"})

    monkeypatch.setattr(speaking_api.requests, "post", post)
    response = client.post("/api/speaking/gemini/client-token")
    assert response.status_code == 200 and response.json()["value"] == "ephemeral-token"
    assert captured["headers"]["x-goog-api-key"] == "private-key"
    assert captured["json"]["uses"] == 1
    assert captured["json"]["liveConnectConstraints"]["model"] == "models/gemini-3.8-live"


def test_gemini_token_errors_are_controlled_and_redacted(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "do-not-leak")
    monkeypatch.setattr(speaking_api.requests, "post", lambda *a, **k: SimpleNamespace(ok=False, status_code=429, text="quota do-not-leak", reason="rate"))
    response = client.post("/api/speaking/gemini/client-token")
    assert response.status_code == 502 and "do-not-leak" not in response.text and "[redacted]" in response.text
    monkeypatch.setattr(speaking_api.requests, "post", lambda *a, **k: (_ for _ in ()).throw(speaking_api.requests.Timeout()))
    assert client.post("/api/speaking/gemini/client-token").status_code == 502


def test_context_selection_prioritizes_again_hard_due_new_and_is_bounded(tmp_path):
    bank, library, _ = stores(tmp_path)
    saved = [bank.save_items([study(text=text)])[0][0] for text in ["new item", "normal due", "hard item", "again item", "future item", "extra item"]]
    set_review(bank, saved[1].id, last_rating="good", due_at=(NOW - timedelta(days=1)).isoformat(), lapse_count=2)
    set_review(bank, saved[2].id, last_rating="hard", due_at=(NOW + timedelta(days=2)).isoformat())
    set_review(bank, saved[3].id, last_rating="again", due_at=(NOW + timedelta(days=2)).isoformat())
    set_review(bank, saved[4].id, last_rating="good", due_at=(NOW + timedelta(days=2)).isoformat())
    context = SpeakingContextBuilder(bank, library, lambda: NOW).build(request(target_limit=4))
    assert [item.text for item in context.target_chunks] == ["again item", "hard item", "normal due", "new item"]
    assert len(context.target_chunks) == 4
    assert "SYSTEM INSTRUCTIONS" in context.system_prompt and "MEDIA CONTENT" in context.system_prompt


def test_media_context_prioritizes_provenance_and_bounds_window_words(tmp_path):
    bank, library, _ = stores(tmp_path)
    item = library.save_item(lesson())
    library.update_listening_progress(item.id, {"last_position_seconds": 8, "current_sentence_id": 8})
    bank.save_items([study(text="global target", library_item_id="other"), study(text="media target", library_item_id=item.id)])
    for index in range(7):
        bank.save_items([study(kind="word", text=f"word {index}")])
    context = SpeakingContextBuilder(bank, library, lambda: NOW).build(request(mode="media", library_item_id=item.id))
    assert context.target_chunks[0].text == "media target"
    assert len(context.media_sentences) == 12 and 8 in [value.sentence_id for value in context.media_sentences]
    assert len(context.support_words) == 5 and context.media_title == "Speaking media"


def test_empty_and_german_context_are_valid(tmp_path):
    bank, library, _ = stores(tmp_path)
    context = SpeakingContextBuilder(bank, library, lambda: NOW).build(request(language="de", level="B1"))
    assert context.language == "de" and context.target_chunks == [] and "shorter questions" in context.system_prompt


@pytest.mark.parametrize("transcript,phrase,expected", [
    ("AS FAR AS I KNOW, yes.", "as far as I know", True),
    ("Straße für Straße", "STRASSE FÜR", True),
    ("as far as", "as far as I know", False),
    ("to my knowledge", "as far as I know", False),
])
def test_target_matching_is_exact_normalized_not_semantic(transcript, phrase, expected):
    assert contains_phrase(transcript, phrase) is expected


def create_session(tmp_path):
    bank, library, sessions = stores(tmp_path)
    target = bank.save_items([study()])[0][0]
    context = SpeakingContextBuilder(bank, library, lambda: NOW).build(request())
    session = sessions.create(user_id="learner", provider="gemini-live", model="gemini-3.8-live", duration=300, context=context, goal=None)
    return sessions, session, target


def test_session_turns_target_evidence_stale_and_finish(tmp_path):
    sessions, session, _ = create_session(tmp_path)
    updated = sessions.append_event("learner", session.id, SpeakingEventRequest(expected_event_count=0, type="turn", role="agent", text="Try to answer naturally."))
    assert updated.target_usage[0].use_count == 0
    updated = sessions.append_event("learner", session.id, SpeakingEventRequest(expected_event_count=1, type="turn", role="learner", text="As far as I know, it works."))
    assert updated.learner_turn_count == 1 and updated.target_usage[0].use_count == 1
    updated = sessions.append_event("learner", session.id, SpeakingEventRequest(expected_event_count=2, type="turn", role="learner", text="As far as I know, perhaps."))
    assert updated.target_usage[0].use_count == 2 and len(updated.target_usage[0].evidence) == 2
    with pytest.raises(SpeakingConflictError):
        sessions.append_event("learner", session.id, SpeakingEventRequest(expected_event_count=2, type="turn", role="learner", text="stale"))
    finished = sessions.finish("learner", session.id, 3, "completed", "user")
    assert finished.status == "completed" and finished.ended_at == NOW and finished.event_count == 4


def test_duplicate_provider_turn_is_idempotent(tmp_path):
    sessions, session, _ = create_session(tmp_path)
    event = SpeakingEventRequest(
        expected_event_count=0, type="turn", role="learner",
        text="As far as I know", provider_turn_id="provider-turn-one",
    )
    updated = sessions.append_event("learner", session.id, event)
    duplicate = sessions.append_event(
        "learner", session.id,
        event.model_copy(update={"expected_event_count": updated.event_count}),
    )
    assert duplicate.event_count == 1
    assert duplicate.learner_turn_count == 1
    assert duplicate.target_usage[0].use_count == 1


def test_session_correction_suggestion_limits_and_no_audio_or_credentials(tmp_path):
    sessions, session, _ = create_session(tmp_path)
    count = 0
    correction = SpeakingCorrection(learner_text="I goes", corrected_text="I go", short_explanation="Agreement", category="grammar")
    suggestion = SpeakingSuggestion(text="that makes sense", meaning="合理", kind="chunk", originating_turn=1)
    for _ in range(8):
        session = sessions.append_event("learner", session.id, SpeakingEventRequest(expected_event_count=count, type="correction", correction=correction)); count += 1
    with pytest.raises(ValueError, match="correction limit"):
        sessions.append_event("learner", session.id, SpeakingEventRequest(expected_event_count=count, type="correction", correction=correction))
    for _ in range(8):
        session = sessions.append_event("learner", session.id, SpeakingEventRequest(expected_event_count=count, type="suggestion", suggestion=suggestion)); count += 1
    payload = sessions.path.read_text()
    assert "audio" not in payload and "api_key" not in payload and "ephemeral" not in payload


def test_storage_malformed_future_atomic_and_recent_bounded(tmp_path):
    _, _, sessions = stores(tmp_path)
    original = "{broken"
    sessions.path.write_text(original)
    with pytest.raises(SpeakingStoreError): sessions.list_recent("learner")
    assert sessions.path.read_text() == original
    sessions.path.write_text(json.dumps({"version": 2, "sessions": []}))
    with pytest.raises(SpeakingStoreError): sessions.list_recent("learner")


def test_tool_dispatcher_is_allowlisted_bounded_and_read_only(tmp_path):
    bank, library, _ = stores(tmp_path)
    item = library.save_item(lesson())
    for index in range(12): bank.save_items([study(text=f"chunk {index}", library_item_id=item.id)])
    dispatcher = ToolDispatcher(bank, library)
    assert len(dispatcher.execute("get_due_chunks", {"language": "en", "limit": 10}, user_id="learner", language="en")) == 10
    media = dispatcher.execute("get_media_context", {"library_item_id": item.id, "sentence_id": 8}, user_id="learner", language="en", library_item_id=item.id)
    assert len(media["sentences"]) <= 12 and "audio_url" not in json.dumps(media)
    before_study, before_library = bank.path.read_text(), library.path.read_text()
    dispatcher.execute("record_correction", {"learner_text": "x", "corrected_text": "y", "short_explanation": "z", "category": "clarity"}, user_id="learner", language="en")
    assert bank.path.read_text() == before_study and library.path.read_text() == before_library
    with pytest.raises(ToolError): dispatcher.execute("shell", {}, user_id="learner", language="en")


@pytest.fixture
def api_stores(tmp_path, monkeypatch):
    bank, library, sessions = stores(tmp_path)
    bank.save_items([study()])
    monkeypatch.setattr(speaking_api, "study_bank", bank)
    monkeypatch.setattr(speaking_api, "library_store", library)
    monkeypatch.setattr(speaking_api, "speaking_store", sessions)
    return sessions


def test_session_api_does_not_call_provider_and_conflicts_are_controlled(api_stores, monkeypatch):
    provider = pytest.fail
    monkeypatch.setattr(speaking_api.requests, "post", provider)
    payload = {"user_id": "learner", "language": "en", "level": "B2", "mode": "review_weak", "provider": "gemini-live", "duration_seconds": 300}
    response = client.post("/api/speaking/sessions", json=payload)
    assert response.status_code == 200 and response.json()["session"]["event_count"] == 0
    session_id = response.json()["session"]["id"]
    event = {"expected_event_count": 0, "type": "turn", "role": "learner", "text": "As far as I know"}
    assert client.post(f"/api/speaking/sessions/{session_id}/events?user_id=learner", json=event).status_code == 200
    assert client.post(f"/api/speaking/sessions/{session_id}/events?user_id=learner", json=event).status_code == 409


def test_session_tool_event_is_validated_and_persisted(api_stores):
    payload = {"user_id": "learner", "language": "en", "level": "B2", "mode": "review_weak", "provider": "gemini-live", "duration_seconds": 300}
    session = client.post("/api/speaking/sessions", json=payload).json()["session"]
    request = {
        "user_id": "learner", "expected_event_count": 0,
        "name": "record_correction", "arguments": {
            "learner_text": "I goes", "corrected_text": "I go",
            "short_explanation": "Agreement", "category": "grammar",
        },
    }
    response = client.post(f"/api/speaking/sessions/{session['id']}/tools", json=request)
    assert response.status_code == 200
    assert response.json()["session"]["event_count"] == 1
    assert response.json()["session"]["corrections"][0]["corrected_text"] == "I go"
    request["expected_event_count"] = 0
    assert client.post(f"/api/speaking/sessions/{session['id']}/tools", json=request).status_code == 409
