import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app as app_module
from services.api import library_api
from services.library.store import LearningLibraryStore
from services.schemas.lesson import Lesson
from services.schemas.shadowing import ShadowingMetrics
from services.transcript.repair import build_repair_plan


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
client = TestClient(app_module.app)


def lesson():
    return Lesson.model_validate({
        "id": "repair", "title": "Repair", "source_url": "https://youtu.be/repair1",
        "source_language": "de", "target_language": "zh-CN", "level": "B2",
        "audio_url": None, "source": "subtitles",
        "sentences": [
            {"id": 1, "text": "Das ist ein", "translation": "这是", "start": 0, "end": 1, "ai_status": "ok"},
            {"id": 2, "text": "vollständiger Satz.", "translation": "完整句子。", "start": 1.1, "end": 2, "ai_status": "ok"},
            {"id": 3, "text": "[musik]", "translation": "", "start": 2, "end": 3, "ai_status": "ok"},
            {"id": 4, "text": "Ja.", "translation": "是。", "start": 4, "end": 4.5, "ai_status": "ok"},
        ],
        "ai": {"provider": "mock", "status": "complete"},
    })


def store(tmp_path):
    return LearningLibraryStore(
        tmp_path / "library.json", tmp_path / "library_media", tmp_path / "media",
        clock=lambda: NOW,
    )


def metrics():
    return ShadowingMetrics(
        recognized_text="Das ist ein", speech_detected=True,
        target_word_count=3, recognized_word_count=3, matched_count=3,
        missed_count=0, extra_count=0, substituted_count=0,
        transcript_match_percent=100, aligned_timed_word_count=0,
        timing_reference_available=False, sync_target_met=False,
    )


def test_repair_plan_maps_by_time_and_marks_colliding_progress_for_reset(tmp_path):
    value = store(tmp_path)
    item = value.save_item(lesson())
    item = value.record_shadowing_attempt(item.id, 1, 0, metrics())
    item = value.record_shadowing_attempt(item.id, 2, 0, metrics())
    plan = build_repair_plan(item)
    assert [sentence.text for sentence in plan.lesson.sentences] == [
        "Das ist ein vollständiger Satz.", "Ja."
    ]
    assert plan.old_to_new == {1: 1, 2: 1, 3: None, 4: 2}
    assert plan.reset_shadowing_sentence_ids == (1, 2)


def test_preview_is_side_effect_free_and_commit_requires_explicit_confirmation(tmp_path, monkeypatch):
    value = store(tmp_path)
    item = value.save_item(lesson())
    monkeypatch.setattr(library_api, "library_store", value)
    original = value.path.read_bytes()
    preview = client.post(f"/api/library/items/{item.id}/repair-transcript", json={})
    assert preview.status_code == 200 and preview.json()["committed"] is False
    assert preview.json()["new_sentence_count"] == 2
    assert value.path.read_bytes() == original
    denied = client.post(
        f"/api/library/items/{item.id}/repair-transcript",
        json={"commit": True, "expected_updated_at": item.updated_at.isoformat()},
    )
    assert denied.status_code == 409 and value.path.read_bytes() == original


def test_confirmed_repair_writes_atomically_and_preserves_non_sentence_state(tmp_path, monkeypatch):
    value = store(tmp_path)
    item = value.save_item(lesson())
    value.update_listening_progress(item.id, {
        "last_position_seconds": 1.5, "current_sentence_id": 2,
    })
    item = value.get_item(item.id)
    monkeypatch.setattr(library_api, "library_store", value)
    response = client.post(
        f"/api/library/items/{item.id}/repair-transcript",
        json={
            "commit": True,
            "confirm_sentence_progress_reset": True,
            "expected_updated_at": item.updated_at.isoformat(),
        },
    )
    assert response.status_code == 200 and response.json()["committed"] is True
    repaired = value.get_item(item.id)
    assert repaired.id == item.id and repaired.progress.training_order == item.progress.training_order
    assert repaired.listening_progress.current_sentence_id == 1
    assert repaired.listening_progress.last_position_seconds == 1.5
    assert repaired.persistent_audio == item.persistent_audio
    assert [sentence.text for sentence in repaired.lesson.sentences] == [
        "Das ist ein vollständiger Satz.", "Ja."
    ]
    assert json.loads(value.path.read_text())["version"] == 3
    assert not value.path.with_suffix(".json.tmp").exists()


def test_stale_repair_commit_cannot_overwrite_newer_library_state(tmp_path, monkeypatch):
    value = store(tmp_path)
    item = value.save_item(lesson())
    monkeypatch.setattr(library_api, "library_store", value)
    response = client.post(
        f"/api/library/items/{item.id}/repair-transcript",
        json={
            "commit": True, "confirm_sentence_progress_reset": True,
            "expected_updated_at": "2026-10-22T11:00:00Z",
        },
    )
    assert response.status_code == 409
    assert len(value.get_item(item.id).lesson.sentences) == 4
