import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.api import library_api
from services.library.store import (
    LearningLibraryStore,
    LibraryStoreError,
    source_key_for_lesson,
)
from services.schemas.library import ListeningProgress
from services.schemas.lesson import Lesson


client = TestClient(app_module.app)
NOW = datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)


def lesson(**updates) -> Lesson:
    payload = {
        "id": "lesson-one",
        "title": "Library lesson",
        "source_url": "https://www.youtube.com/watch?v=abc_123",
        "source_language": "en",
        "target_language": "zh-CN",
        "level": "B2",
        "audio_url": None,
        "source": "subtitles",
        "sentences": [
            {
                "id": 1,
                "text": "A useful sentence.",
                "translation": "一个有用的句子。",
                "start": 1.0,
                "end": 4.5,
                "keywords": [{"word": "useful", "meaning": "有用"}],
                "chunks": [{"text": "a useful", "meaning": "一个有用的"}],
                "grammar": "article + adjective",
                "ai_status": "ok",
            }
        ],
        "training_order": [
            "active_recall",
            "blind_listening",
            "comprehension",
            "mini_dictation",
            "shadowing",
            "retell",
            "live_dialogue",
        ],
        "ai": {"provider": "mock", "status": "complete"},
    }
    payload.update(updates)
    return Lesson.model_validate(payload)


def store(tmp_path: Path, *, clock=lambda: NOW) -> LearningLibraryStore:
    return LearningLibraryStore(
        tmp_path / "learning_library.json",
        tmp_path / "library_media",
        tmp_path / "media",
        clock=clock,
    )


def progress(stage=0, sentence=0, completed=None):
    return {
        "is_training": True,
        "stage_index": stage,
        "sentence_index": sentence,
        "completed_stages": completed or [],
        "training_order": lesson().training_order,
        "lesson_memory_imported": True,
    }


def test_create_and_full_lesson_round_trip(tmp_path):
    library = store(tmp_path)
    original = lesson()

    created = library.save_item(original)

    assert created.lesson == original
    assert library.get_item(created.id) == created
    assert json.loads(library.path.read_text())["version"] == 2


def test_list_is_lightweight_recent_first_summary(tmp_path):
    times = iter([NOW, LATER])
    library = store(tmp_path, clock=lambda: next(times))
    library.save_item(lesson())
    recent = library.save_item(
        lesson(id="two", source_url="https://youtu.be/def_456", title="Recent")
    )

    summaries = library.list_summaries()

    assert summaries[0].id == recent.id
    assert summaries[0].sentence_count == 1
    assert summaries[0].duration_seconds == 4.5
    assert "lesson" not in summaries[0].model_dump()


def test_youtube_source_key_upserts_variants_and_preserves_identity(tmp_path):
    times = iter([NOW, LATER])
    library = store(tmp_path, clock=lambda: next(times))
    first = library.save_item(lesson())
    second = library.save_item(
        lesson(
            id="regenerated",
            title="Regenerated",
            source_url="https://youtu.be/abc_123?t=12",
        )
    )

    assert second.id == first.id
    assert second.created_at == NOW
    assert second.updated_at == LATER
    assert second.lesson.title == "Regenerated"
    assert len(library.list_items()) == 1


def test_different_levels_have_separate_source_keys(tmp_path):
    library = store(tmp_path)
    b2 = library.save_item(lesson())
    c1 = library.save_item(lesson(id="c1", level="C1"))

    assert b2.source_key != c1.source_key
    assert len(library.list_items()) == 2


def test_non_youtube_normalization_is_conservative():
    first = lesson(source_url="HTTPS://Example.COM/path?a=1#fragment")
    second = lesson(source_url="https://example.com/path?a=2")

    assert source_key_for_lesson(first) != source_key_for_lesson(second)
    assert "#fragment" not in source_key_for_lesson(first)


def test_progress_update_persists_without_changing_lesson(tmp_path):
    library = store(tmp_path)
    item = library.save_item(lesson())
    original_lesson = item.lesson

    updated = library.update_progress(
        item.id,
        progress(stage=1, completed=["active_recall"]),
    )

    assert updated.progress.stage_index == 1
    assert updated.lesson == original_lesson
    assert library.get_item(item.id).progress == updated.progress


def test_progress_update_rejects_naive_timestamp_without_corruption(tmp_path):
    times = iter([NOW, datetime(2026, 10, 17, 9, 0)])
    library = store(tmp_path, clock=lambda: next(times))
    item = library.save_item(lesson())

    with pytest.raises(LibraryStoreError, match="timezone-aware"):
        library.update_progress(item.id, progress(stage=1, completed=["active_recall"]))

    restored = library.get_item(item.id)
    assert restored.updated_at == NOW
    assert restored.progress.stage_index == 0


def test_invalid_progress_update_is_rejected_without_corruption(tmp_path):
    library = store(tmp_path)
    item = library.save_item(lesson())

    with pytest.raises(ValueError):
        library.update_progress(item.id, progress(stage=99))

    assert library.get_item(item.id).lesson == item.lesson
    assert library.get_item(item.id).progress.stage_index == 0


def test_invalid_stored_progress_resets_only_progress(tmp_path):
    library = store(tmp_path)
    item = library.save_item(lesson())
    payload = json.loads(library.path.read_text())
    payload["items"][0]["progress"]["stage_index"] = 99
    library.path.write_text(json.dumps(payload), encoding="utf-8")

    restored = library.get_item(item.id)

    assert restored.lesson == item.lesson
    assert restored.progress.stage_index == 0
    assert restored.progress.is_training is False


def test_v1_library_remains_readable_without_rewriting_and_gets_listening_defaults(tmp_path):
    library = store(tmp_path)
    item = library.save_item(lesson())
    payload = json.loads(library.path.read_text())
    payload["version"] = 1
    payload["items"][0].pop("listening_progress")
    original = json.dumps(payload)
    library.path.write_text(original, encoding="utf-8")

    restored = library.get_item(item.id)

    assert restored.listening_progress == ListeningProgress()
    assert library.path.read_text(encoding="utf-8") == original


def test_next_safe_v1_mutation_migrates_to_v2_and_preserves_guided_progress(tmp_path):
    library = store(tmp_path)
    item = library.save_item(lesson(), progress(stage=1, completed=["active_recall"]))
    payload = json.loads(library.path.read_text())
    payload["version"] = 1
    payload["items"][0].pop("listening_progress")
    library.path.write_text(json.dumps(payload), encoding="utf-8")

    updated = library.update_listening_progress(
        item.id,
        {
            "last_position_seconds": 2.5,
            "current_sentence_id": 1,
            "playback_rate": 0.75,
            "source_visible": False,
            "translation_visible": True,
            "mode": "blind",
        },
    )

    assert updated.progress.stage_index == 1
    assert updated.progress.completed_stages == ["active_recall"]
    assert json.loads(library.path.read_text())["version"] == 2


def test_v1_to_v2_migration_preserves_persistent_audio(tmp_path):
    library = store(tmp_path)
    library.source_media_root.mkdir(parents=True)
    (library.source_media_root / "lesson.mp3").write_bytes(b"persistent")
    item = library.save_item(lesson(audio_url="/media/lesson.mp3"))
    payload = json.loads(library.path.read_text())
    payload["version"] = 1
    payload["items"][0].pop("listening_progress")
    library.path.write_text(json.dumps(payload), encoding="utf-8")

    updated = library.update_listening_progress(
        item.id, {"last_position_seconds": 2, "current_sentence_id": 1}
    )

    assert updated.persistent_audio == item.persistent_audio
    assert library.audio_path(item.id)[0].read_bytes() == b"persistent"


def test_v2_listening_progress_round_trip_and_summary(tmp_path):
    library = store(tmp_path)
    item = library.save_item(lesson())
    expected = {
        "last_position_seconds": 2.5,
        "current_sentence_id": 1,
        "playback_rate": 1.25,
        "source_visible": True,
        "translation_visible": False,
        "mode": "dictation",
    }

    updated = library.update_listening_progress(item.id, expected)
    summary = library.list_summaries()[0]

    assert updated.listening_progress.model_dump() == expected
    assert library.get_item(item.id).listening_progress == updated.listening_progress
    assert summary.last_position_seconds == 2.5
    assert summary.has_listening_progress is True


def test_listening_update_preserves_lesson_guided_progress_and_audio(tmp_path):
    library = store(tmp_path)
    library.source_media_root.mkdir(parents=True)
    (library.source_media_root / "lesson.mp3").write_bytes(b"audio")
    item = library.save_item(
        lesson(audio_url="/media/lesson.mp3"),
        progress(stage=1, completed=["active_recall"]),
    )

    updated = library.update_listening_progress(
        item.id,
        {"last_position_seconds": 2, "current_sentence_id": 1},
    )

    assert updated.lesson == item.lesson
    assert updated.progress == item.progress
    assert updated.persistent_audio == item.persistent_audio
    assert library.audio_path(item.id)[0].read_bytes() == b"audio"


@pytest.mark.parametrize(
    "patch,error",
    [
        ({"last_position_seconds": -1}, "Invalid Listening progress"),
        ({"last_position_seconds": 99}, "Listening position is out of range"),
        ({"current_sentence_id": 999}, "Listening sentence id is out of range"),
        ({"playback_rate": 2}, "Invalid Listening progress"),
        ({"mode": "shadowing"}, "Invalid Listening progress"),
    ],
)
def test_invalid_listening_update_is_rejected_without_corruption(tmp_path, patch, error):
    library = store(tmp_path)
    item = library.save_item(lesson())

    with pytest.raises(ValueError, match=error):
        library.update_listening_progress(
            item.id, {**ListeningProgress().model_dump(), **patch}
        )

    assert library.get_item(item.id) == item


def test_invalid_stored_listening_progress_resets_only_listening_state(tmp_path):
    library = store(tmp_path)
    item = library.save_item(lesson(), progress(stage=1, completed=["active_recall"]))
    payload = json.loads(library.path.read_text())
    payload["items"][0]["listening_progress"]["last_position_seconds"] = 999
    library.path.write_text(json.dumps(payload), encoding="utf-8")

    restored = library.get_item(item.id)

    assert restored.lesson == item.lesson
    assert restored.progress == item.progress
    assert restored.listening_progress == ListeningProgress()


def test_listening_update_has_deterministic_updated_at(tmp_path):
    times = iter([NOW, LATER])
    library = store(tmp_path, clock=lambda: next(times))
    item = library.save_item(lesson())

    updated = library.update_listening_progress(
        item.id, {"last_position_seconds": 2, "current_sentence_id": 1}
    )

    assert item.updated_at == NOW
    assert updated.updated_at == LATER


def test_regenerating_lesson_preserves_listening_state(tmp_path):
    times = iter([NOW, LATER, datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)])
    library = store(tmp_path, clock=lambda: next(times))
    item = library.save_item(lesson())
    library.update_listening_progress(
        item.id, {"last_position_seconds": 2, "current_sentence_id": 1}
    )

    regenerated = library.save_item(lesson(title="Regenerated"))

    assert regenerated.listening_progress.last_position_seconds == 2


def test_delete_removes_only_selected_item(tmp_path):
    library = store(tmp_path)
    first = library.save_item(lesson())
    second = library.save_item(lesson(id="two", source_url="https://youtu.be/def_456"))

    library.delete_item(first.id)

    assert [item.id for item in library.list_items()] == [second.id]


@pytest.mark.parametrize(
    "payload,error",
    [
        ("{broken", "Malformed"),
        (json.dumps({"version": 99, "items": []}), "Unsupported"),
    ],
)
def test_unreadable_store_is_not_overwritten(tmp_path, payload, error):
    library = store(tmp_path)
    library.path.write_text(payload, encoding="utf-8")

    with pytest.raises(LibraryStoreError, match=error):
        library.save_item(lesson())

    assert library.path.read_text(encoding="utf-8") == payload


def test_writes_use_temporary_file_and_atomic_replace(tmp_path, monkeypatch):
    library = store(tmp_path)
    calls = []
    real_replace = os.replace

    def replace(source, destination):
        calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr("services.library.store.os.replace", replace)
    library.save_item(lesson())

    assert calls[-1][0].suffix == ".tmp"
    assert calls[-1][1] == library.path
    assert not calls[-1][0].exists()


def test_process_local_lock_serializes_concurrent_upserts(tmp_path):
    library = store(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: library.save_item(lesson()), range(20)))

    assert len(library.list_items()) == 1


def test_process_local_lock_serializes_listening_updates(tmp_path):
    library = store(tmp_path)
    item = library.save_item(lesson())

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(
            lambda position: library.update_listening_progress(
                item.id,
                {"last_position_seconds": position, "current_sentence_id": 1},
            ),
            [value / 10 for value in range(20)],
        ))

    assert 0 <= library.get_item(item.id).listening_progress.last_position_seconds <= 1.9
    assert json.loads(library.path.read_text())["version"] == 2


def test_safe_media_audio_is_copied_and_survives_source_deletion(tmp_path):
    library = store(tmp_path)
    source = library.source_media_root / "lesson.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"durable audio")

    item = library.save_item(lesson(audio_url="/media/lesson.mp3"))
    source.unlink()
    audio_path, content_type = library.audio_path(item.id)

    assert audio_path.read_bytes() == b"durable audio"
    assert content_type == "audio/mpeg"
    assert library.item_for_response(item).lesson.audio_url.endswith("/audio")


@pytest.mark.parametrize(
    "audio_url",
    [
        "/media/../secret.mp3",
        "/tmp/secret.mp3",
        "https://example.com/audio.mp3",
        "data:audio/mp3;base64,AAAA",
        "blob:http://localhost/audio",
    ],
)
def test_unsafe_audio_references_are_not_copied(tmp_path, audio_url):
    library = store(tmp_path)
    (tmp_path / "secret.mp3").write_bytes(b"secret")

    item = library.save_item(lesson(audio_url=audio_url))

    assert item.persistent_audio.status == "unavailable"
    assert not (library.media_root / item.id).exists()


def test_symlink_audio_escape_is_rejected(tmp_path):
    library = store(tmp_path)
    library.source_media_root.mkdir(parents=True)
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"secret")
    link = library.source_media_root / "link.mp3"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")

    item = library.save_item(lesson(audio_url="/media/link.mp3"))

    assert item.persistent_audio.status == "unavailable"


def test_missing_source_audio_still_saves_lesson(tmp_path):
    library = store(tmp_path)

    item = library.save_item(lesson(audio_url="/media/missing.mp3"))

    assert item.lesson.title == "Library lesson"
    assert item.persistent_audio.status == "unavailable"


def test_deleting_item_removes_only_its_owned_audio(tmp_path):
    library = store(tmp_path)
    library.source_media_root.mkdir(parents=True)
    (library.source_media_root / "one.mp3").write_bytes(b"one")
    (library.source_media_root / "two.mp3").write_bytes(b"two")
    first = library.save_item(lesson(audio_url="/media/one.mp3"))
    second = library.save_item(
        lesson(id="two", source_url="https://youtu.be/def_456", audio_url="/media/two.mp3")
    )

    library.delete_item(first.id)

    assert not (library.media_root / first.id).exists()
    assert library.audio_path(second.id)[0].read_bytes() == b"two"


@pytest.fixture
def api_store(tmp_path, monkeypatch):
    value = store(tmp_path)
    monkeypatch.setattr(library_api, "library_store", value)
    return value


def test_library_api_create_list_get_progress_delete(api_store):
    created = client.post("/api/library/items", json={"lesson": lesson().model_dump(mode="json")})
    assert created.status_code == 200
    item_id = created.json()["id"]

    listed = client.get("/api/library/items")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["lesson_id"] == "lesson-one"
    assert "lesson" not in listed.json()["items"][0]

    fetched = client.get(f"/api/library/items/{item_id}")
    assert fetched.json()["lesson"]["sentences"][0]["text"] == "A useful sentence."

    patched = client.patch(
        f"/api/library/items/{item_id}/progress",
        json=progress(stage=1, completed=["active_recall"]),
    )
    assert patched.status_code == 200
    assert patched.json()["progress"]["stage_index"] == 1

    listening = client.patch(
        f"/api/library/items/{item_id}/listening-progress",
        json={
            "last_position_seconds": 2,
            "current_sentence_id": 1,
            "playback_rate": 0.75,
            "source_visible": False,
            "translation_visible": True,
            "mode": "blind",
        },
    )
    assert listening.status_code == 200
    assert listening.json()["listening_progress"]["mode"] == "blind"
    assert listening.json()["progress"]["stage_index"] == 1

    deleted = client.delete(f"/api/library/items/{item_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/library/items/{item_id}").status_code == 404


def test_library_audio_endpoint_serves_only_saved_audio(api_store):
    api_store.source_media_root.mkdir(parents=True)
    (api_store.source_media_root / "lesson.mp3").write_bytes(b"saved audio")
    response = client.post(
        "/api/library/items",
        json={"lesson": lesson(audio_url="/media/lesson.mp3").model_dump(mode="json")},
    )
    item_id = response.json()["id"]

    audio = client.get(f"/api/library/items/{item_id}/audio")

    assert audio.status_code == 200
    assert audio.content == b"saved audio"
    assert client.get("/api/library/items/missing/audio").status_code == 404


def test_library_api_invalid_progress_does_not_change_lesson(api_store):
    item = client.post(
        "/api/library/items", json={"lesson": lesson().model_dump(mode="json")}
    ).json()

    response = client.patch(
        f"/api/library/items/{item['id']}/progress",
        json=progress(stage=99),
    )

    assert response.status_code == 422
    restored = client.get(f"/api/library/items/{item['id']}").json()
    assert restored["lesson"] == item["lesson"]


def test_library_api_rejects_invalid_listening_position(api_store):
    item = client.post(
        "/api/library/items", json={"lesson": lesson().model_dump(mode="json")}
    ).json()

    response = client.patch(
        f"/api/library/items/{item['id']}/listening-progress",
        json={**ListeningProgress().model_dump(), "last_position_seconds": 99},
    )

    assert response.status_code == 422
    assert client.get(f"/api/library/items/{item['id']}").json()["lesson"] == item["lesson"]
