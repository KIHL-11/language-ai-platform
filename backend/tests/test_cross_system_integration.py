import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app as app_module
from services.api import chunk_api, library_api, speaking_api, study_bank_api
from services.library.store import LearningLibraryStore
from services.retrieval.chunk_memory import ChunkMemoryService
from services.retrieval.chunk_store import ChunkStore
from services.schemas.speaking import (
    SpeakingContext,
    SpeakingEventRequest,
    SpeakingSuggestion,
)
from services.schemas.lesson import Lesson
from services.schemas.study_bank import StudyItemInput, StudyProvenance
from services.speaking.store import SpeakingSessionStore
from services.study_bank.store import StudyBankStore


NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
client = TestClient(app_module.app)


def media_provenance():
    return StudyProvenance(
        library_item_id="missing-media",
        lesson_id="lesson-one",
        source_url="https://youtu.be/example",
        media_title="Example media",
        sentence_id=1,
        sentence_text="As far as I know, it works.",
        sentence_translation="据我所知，它可以工作。",
        start=1,
        end=3,
    )


def study(kind="chunk", text="as far as I know", language="en"):
    return StudyItemInput(
        user_id="learner",
        kind=kind,
        source_language=language,
        target_language="zh-CN",
        text=text,
        meaning="据我所知",
        provenance=[media_provenance()],
    )


def configure_stores(tmp_path, monkeypatch):
    study_store = StudyBankStore(
        tmp_path / "study.json", clock=lambda: NOW, id_factory=lambda: "study-one"
    )
    library_store = LearningLibraryStore(
        tmp_path / "library.json",
        tmp_path / "library_media",
        tmp_path / "media",
        clock=lambda: NOW,
    )
    speaking_store = SpeakingSessionStore(
        tmp_path / "speaking.json", clock=lambda: NOW, id_factory=lambda: "session-one"
    )
    memory = ChunkMemoryService(
        ChunkStore(tmp_path / "chunks.json"),
        clock=lambda: NOW,
        id_factory=lambda: "chunk-one",
    )
    monkeypatch.setattr(study_bank_api, "study_bank_store", study_store)
    monkeypatch.setattr(library_api, "library_store", library_store)
    monkeypatch.setattr(speaking_api, "speaking_store", speaking_store)
    monkeypatch.setattr(chunk_api, "chunk_memory_service", memory)
    return study_store, library_store, speaking_store, memory


def completed_session(store: SpeakingSessionStore, *, suggestion_kind="chunk"):
    context = SpeakingContext(
        language="en",
        level="B2",
        mode="review_weak",
        session_goal="Practice",
        target_chunks=[],
        support_words=[],
        media_sentences=[],
        system_prompt="SYSTEM INSTRUCTIONS",
    )
    session = store.create(
        user_id="learner",
        provider="gemini-live",
        model="gemini-3.8-live",
        duration=300,
        context=context,
        goal=None,
    )
    session = store.append_event(
        "learner",
        session.id,
        SpeakingEventRequest(
            expected_event_count=0,
            type="turn",
            role="learner",
            text="That makes sense to me.",
        ),
    )
    session = store.append_event(
        "learner",
        session.id,
        SpeakingEventRequest(
            expected_event_count=1,
            type="suggestion",
            suggestion=SpeakingSuggestion(
                text="that makes sense" if suggestion_kind == "chunk" else "insightful",
                meaning="有道理",
                kind=suggestion_kind,
                short_usage_note="A natural reaction",
                originating_turn=1,
            ),
        ),
    )
    return store.finish("learner", session.id, 2, "completed", "user")


def test_empty_overview_is_bounded_and_read_only(tmp_path, monkeypatch):
    study_store, library_store, speaking_store, memory = configure_stores(tmp_path, monkeypatch)
    before = [
        study_store.path.read_text(),
        library_store.path.read_text(),
        speaking_store.path.read_text(),
        memory.store.path.read_text(),
    ]
    response = client.get("/api/integration/overview", params={"user_id": "learner"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["weak_chunks"] == []
    assert payload["continue_listening"] == []
    assert payload["speaking_suggestions"] == []
    assert payload["next_actions"] == []
    assert [
        study_store.path.read_text(),
        library_store.path.read_text(),
        speaking_store.path.read_text(),
        memory.store.path.read_text(),
    ] == before


def test_overview_uses_review_and_recent_transcript_backed_evidence(tmp_path, monkeypatch):
    study_store, _, speaking_store, _ = configure_stores(tmp_path, monkeypatch)
    item = study_store.save_items([study()])[0][0]
    study_store.rate_item("learner", item.id, "again", 0)
    context = SpeakingContext(
        language="en", level="B2", mode="review_weak", session_goal="Practice",
        target_chunks=[{"study_item_id": item.id, "text": item.text, "meaning": item.meaning}],
        support_words=[], media_sentences=[], system_prompt="SYSTEM INSTRUCTIONS",
    )
    session = speaking_store.create(user_id="learner", provider="gemini-live", model="gemini-3.8-live", duration=300, context=context, goal=None)
    session = speaking_store.append_event("learner", session.id, SpeakingEventRequest(expected_event_count=0, type="turn", role="agent", text="Try it."))
    assert session.target_usage[0].use_count == 0
    speaking_store.append_event("learner", session.id, SpeakingEventRequest(expected_event_count=1, type="turn", role="learner", text="As far as I know, yes."))

    response = client.get("/api/integration/overview", params={"user_id": "learner", "language": "en"})
    assert response.status_code == 200
    weak = response.json()["weak_chunks"]
    assert len(weak) == 1
    assert weak[0]["last_rating"] == "again"
    assert weak[0]["recent_targeted_session_count"] == 1
    assert weak[0]["recent_used_session_count"] == 1
    assert weak[0]["speaking_evidence"] == "As far as I know, yes."
    assert len(response.json()["next_actions"]) <= 6
    assert client.get("/api/integration/overview", params={"user_id": "learner", "language": "de"}).json()["weak_chunks"] == []


def test_speaking_suggestion_save_is_explicit_idempotent_and_syncs_chunk(tmp_path, monkeypatch):
    study_store, _, speaking_store, memory = configure_stores(tmp_path, monkeypatch)
    session = completed_session(speaking_store)
    route = f"/api/integration/speaking/sessions/{session.id}/suggestions/0/save"
    request = {"user_id": "learner", "target_language": "zh-CN"}

    first = client.post(route, json=request)
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["chunk_memory_synchronized"] is True
    item = study_store.list_items("learner")[0]
    assert item.review.review_count == 0 and item.review.due_at == item.created_at
    assert item.provenance[0].source_type == "speaking"
    assert item.provenance[0].speaking_session_id == session.id
    chunk = memory.list_chunks("learner")[0]
    assert chunk.source_stage == "speaking"
    assert chunk.source_lesson_id is None and chunk.source_media_id is None and chunk.source_sentence_id is None

    second = client.post(route, json=request)
    assert second.status_code == 200 and second.json()["created"] is False
    repeated = study_store.list_items("learner")[0]
    assert repeated.id == item.id and repeated.review == item.review
    assert len(repeated.provenance) == 1 and len(memory.list_chunks("learner")) == 1
    assert json.loads(study_store.path.read_text())["version"] == 3


def test_suggestion_save_rejects_forgery_wrong_owner_and_active_session(tmp_path, monkeypatch):
    _, _, speaking_store, _ = configure_stores(tmp_path, monkeypatch)
    session = completed_session(speaking_store)
    base = f"/api/integration/speaking/sessions/{session.id}/suggestions"
    assert client.post(f"{base}/9/save", json={"user_id": "learner", "target_language": "zh-CN"}).status_code == 404
    assert client.post(f"{base}/0/save", json={"user_id": "other", "target_language": "zh-CN"}).status_code == 404

    active_store = SpeakingSessionStore(tmp_path / "active.json", clock=lambda: NOW, id_factory=lambda: "active")
    active = completed_session(active_store)
    payload = json.loads(active_store.path.read_text())
    payload["sessions"][0].update({"status": "active", "ended_at": None, "end_reason": None})
    active_store.path.write_text(json.dumps(payload))
    monkeypatch.setattr(speaking_api, "speaking_store", active_store)
    assert client.post(f"/api/integration/speaking/sessions/{active.id}/suggestions/0/save", json={"user_id": "learner", "target_language": "zh-CN"}).status_code == 409


def test_speaking_word_does_not_create_chunk_memory(tmp_path, monkeypatch):
    study_store, _, speaking_store, memory = configure_stores(tmp_path, monkeypatch)
    session = completed_session(speaking_store, suggestion_kind="word")
    response = client.post(
        f"/api/integration/speaking/sessions/{session.id}/suggestions/0/save",
        json={"user_id": "learner", "target_language": "zh-CN"},
    )
    assert response.status_code == 200
    assert response.json()["chunk_memory_synchronized"] is False
    assert study_store.list_items("learner")[0].kind == "word"
    assert memory.list_chunks("learner") == []


def test_speaking_provenance_merges_with_media_without_resetting_review(tmp_path, monkeypatch):
    study_store, _, speaking_store, memory = configure_stores(tmp_path, monkeypatch)
    original = study_store.save_items([study(text="that makes sense")])[0][0]
    rated, _ = study_store.rate_item("learner", original.id, "good", 0)
    session = completed_session(speaking_store)
    response = client.post(
        f"/api/integration/speaking/sessions/{session.id}/suggestions/0/save",
        json={"user_id": "learner", "target_language": "zh-CN", "note": "keep practicing"},
    )
    assert response.status_code == 200 and response.json()["created"] is False
    merged = study_store.get_item("learner", original.id)
    assert merged.review == rated.review
    assert {source.source_type for source in merged.provenance} == {"media", "speaking"}
    assert len(memory.list_chunks("learner")) == 1


def test_partial_chunk_sync_error_does_not_erase_saved_study_item(tmp_path, monkeypatch):
    study_store, _, speaking_store, memory = configure_stores(tmp_path, monkeypatch)
    session = completed_session(speaking_store)
    monkeypatch.setattr(memory, "save_chunk", lambda **kwargs: (_ for _ in ()).throw(ValueError("disk")))
    response = client.post(
        f"/api/integration/speaking/sessions/{session.id}/suggestions/0/save",
        json={"user_id": "learner", "target_language": "zh-CN"},
    )
    assert response.status_code == 500
    assert "saved to Study Bank" in response.json()["detail"]
    assert len(study_store.list_items("learner")) == 1


def test_evidence_enforces_ownership_and_target_languages_are_explicit(tmp_path, monkeypatch):
    study_store, _, _, _ = configure_stores(tmp_path, monkeypatch)
    item = study_store.save_items([study()])[0][0]
    assert client.get(
        f"/api/integration/study-items/{item.id}", params={"user_id": "other"}
    ).status_code == 404
    response = client.get(
        "/api/integration/target-languages",
        params={"user_id": "learner", "source_language": "en"},
    )
    assert response.status_code == 200
    assert response.json() == {"target_languages": ["zh-CN"]}


def test_repaired_sentence_id_cannot_misdirect_historical_today_link(tmp_path, monkeypatch):
    study_store, library_store, _, _ = configure_stores(tmp_path, monkeypatch)
    saved = library_store.save_item(Lesson.model_validate({
        "id": "repair", "title": "Repair", "source_url": "https://youtu.be/repair1",
        "source_language": "en", "target_language": "zh-CN", "level": "B2",
        "audio_url": None, "source": "subtitles",
        "sentences": [
            {"id": 1, "text": "An unfinished", "start": 0, "end": 1},
            {"id": 2, "text": "sentence.", "start": 1.1, "end": 2},
            {"id": 3, "text": "Another sentence.", "start": 4, "end": 5},
        ],
        "ai": {"provider": "mock", "status": "complete"},
    }))
    provenance = media_provenance().model_copy(update={
        "library_item_id": saved.id,
        "lesson_id": saved.lesson.id,
        "sentence_id": 2,
        "sentence_text": "sentence.",
        "start": 1.1,
        "end": 2,
    })
    study_item = study_store.save_items([
        study().model_copy(update={"provenance": [provenance]})
    ])[0][0]
    before = study_store.path.read_bytes()
    preview = client.post(f"/api/library/items/{saved.id}/repair-transcript", json={}).json()
    response = client.post(
        f"/api/library/items/{saved.id}/repair-transcript",
        json={
            "commit": True,
            "confirm_sentence_progress_reset": True,
            "expected_updated_at": preview["expected_updated_at"],
        },
    )
    assert response.status_code == 200
    assert study_store.path.read_bytes() == before
    evidence = client.get(
        f"/api/integration/study-items/{study_item.id}",
        params={"user_id": "learner"},
    ).json()
    assert evidence["preferred_shadowing_source"] is None
    assert evidence["source_sentence_count"] == 0
