import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app as app_module
from services.api import study_bank_api
from services.api import chunk_api
from services.retrieval.chunk_memory import ChunkMemoryService
from services.retrieval.chunk_store import ChunkStore
from services.review.scheduler import DAY, MAX_INTERVAL_SECONDS, schedule_review
from services.review.service import ReviewService
from services.schemas.study_bank import (
    ReviewState,
    StudyItemInput,
    StudyItemPatch,
    StudyProvenance,
)
from services.study_bank.store import StudyBankStore, StudyBankStoreError


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
client = TestClient(app_module.app)


def provenance(**updates):
    value = {
        "library_item_id": "library-one",
        "lesson_id": "lesson-one",
        "source_url": "https://www.youtube.com/watch?v=one",
        "media_title": "Daily media",
        "sentence_id": 1,
        "sentence_text": "We can figure it out.",
        "sentence_translation": "我们可以解决。",
        "start": 1,
        "end": 3,
    }
    value.update(updates)
    return StudyProvenance(**value)


def study_input(kind="word", **updates):
    value = {
        "user_id": "learner-one",
        "kind": kind,
        "source_language": "en",
        "target_language": "zh-CN",
        "text": "figuring" if kind == "word" else "figure it out",
        "meaning": "解决",
        "lemma": "figure" if kind == "word" else None,
        "part_of_speech": "verb" if kind == "word" else None,
        "example": "We can figure it out." if kind == "word" else None,
        "usage": "solve something" if kind == "chunk" else None,
        "provenance": [provenance()],
    }
    value.update(updates)
    return StudyItemInput(**value)


def store(tmp_path, clock=lambda: NOW):
    ids = iter(f"item-{index:03}" for index in range(100))
    return StudyBankStore(
        tmp_path / "study_bank.json",
        clock=clock,
        id_factory=lambda: next(ids),
    )


def reviewed_state(
    *, interval=DAY, ease=2.5, due_at=None, rating="good", count=1,
):
    counters = {"again": 0, "hard": 0, "good": 0, "easy": 0}
    counters[rating] = count
    return ReviewState(
        due_at=due_at or NOW,
        interval_seconds=interval,
        ease_factor=ease,
        repetition_count=count,
        lapse_count=0,
        review_count=count,
        again_count=counters["again"],
        hard_count=counters["hard"],
        good_count=counters["good"],
        easy_count=counters["easy"],
        last_reviewed_at=NOW - timedelta(days=1),
        last_rating=rating,
    )


def v1_payload(item):
    record = item.model_dump(mode="json")
    record.pop("review")
    return {"version": 1, "items": [record]}


def test_v1_is_read_only_until_next_mutation_and_migrates_without_data_loss(tmp_path):
    value = store(tmp_path)
    item = value.save_items([study_input(note="mine")])[0][0]
    original = json.dumps(v1_payload(item), ensure_ascii=False, indent=2) + "\n"
    value.path.write_text(original, encoding="utf-8")

    loaded = value.list_items("learner-one")[0]

    assert value.path.read_text(encoding="utf-8") == original
    assert loaded.id == item.id and loaded.note == "mine"
    assert loaded.provenance == item.provenance and loaded.active is True
    assert loaded.review == ReviewState.new(item.created_at)

    value.update_item("learner-one", item.id, StudyItemPatch(meaning="edited"))
    migrated = json.loads(value.path.read_text(encoding="utf-8"))
    assert migrated["version"] == 2
    assert migrated["items"][0]["id"] == item.id
    assert migrated["items"][0]["review"]["review_count"] == 0


@pytest.mark.parametrize(
    "payload,message",
    [
        ({"version": 1, "items": [{"broken": True}]}, "Malformed"),
        ({"version": 2, "items": [{"broken": True}]}, "Malformed"),
        ({"version": 3, "items": []}, "Unsupported"),
    ],
)
def test_invalid_or_future_storage_is_never_overwritten(tmp_path, payload, message):
    value = store(tmp_path)
    original = json.dumps(payload)
    value.path.write_text(original, encoding="utf-8")
    with pytest.raises(StudyBankStoreError, match=message):
        value.save_items([study_input()])
    assert value.path.read_text(encoding="utf-8") == original


def test_new_items_are_v2_and_due_immediately_for_words_and_chunks(tmp_path):
    value = store(tmp_path)
    items, _ = value.save_items([study_input(), study_input("chunk")])
    assert json.loads(value.path.read_text())["version"] == 2
    assert [(item.kind, item.review.due_at, item.review.review_count) for item in items] == [
        ("word", NOW, 0), ("chunk", NOW, 0),
    ]


def test_merge_edit_archive_and_restore_preserve_review_state(tmp_path):
    times = iter(NOW + timedelta(minutes=index) for index in range(6))
    value = store(tmp_path, clock=lambda: next(times))
    item = value.save_items([study_input()])[0][0]
    rated, _ = value.rate_item("learner-one", item.id, "good", 0)
    merged = value.save_items([study_input(provenance=[provenance(library_item_id="two")])])[0][0]
    edited = value.update_item("learner-one", item.id, StudyItemPatch(meaning="new", note="note"))
    archived = value.update_item("learner-one", item.id, StudyItemPatch(active=False))
    restored = value.update_item("learner-one", item.id, StudyItemPatch(active=True))
    assert len(merged.provenance) == 2
    assert merged.review == edited.review == archived.review == restored.review == rated.review


@pytest.mark.parametrize(
    "rating,previous,expected_interval,expected_ease,repetitions,lapses",
    [
        ("again", ReviewState.new(NOW), 600, 2.3, 0, 1),
        ("again", reviewed_state(ease=1.3), 600, 1.3, 0, 1),
        ("hard", ReviewState.new(NOW), DAY, 2.35, 1, 0),
        ("hard", reviewed_state(interval=10 * DAY), 12 * DAY, 2.35, 2, 0),
        ("hard", reviewed_state(interval=DAY, ease=1.3), round(DAY * 1.20), 1.3, 2, 0),
        ("good", ReviewState.new(NOW), DAY, 2.5, 1, 0),
        ("good", reviewed_state(interval=DAY), 3 * DAY, 2.5, 2, 0),
        ("good", reviewed_state(interval=4 * DAY), 10 * DAY, 2.5, 2, 0),
        ("easy", ReviewState.new(NOW), 4 * DAY, 2.65, 1, 0),
        ("easy", reviewed_state(interval=4 * DAY), round(4 * DAY * 2.5 * 1.3), 2.65, 2, 0),
        ("easy", reviewed_state(interval=4 * DAY, ease=3.0), round(4 * DAY * 3.0 * 1.3), 3.0, 2, 0),
    ],
)
def test_scheduler_rules(rating, previous, expected_interval, expected_ease, repetitions, lapses):
    result = schedule_review(previous, rating, NOW)
    assert result.interval_seconds == expected_interval
    assert result.due_at == NOW + timedelta(seconds=expected_interval)
    assert result.ease_factor == pytest.approx(expected_ease)
    assert result.repetition_count == repetitions
    assert result.lapse_count == lapses
    assert result.review_count == previous.review_count + 1
    assert result.last_rating == rating and result.last_reviewed_at == NOW
    assert getattr(result, f"{rating}_count") == getattr(previous, f"{rating}_count") + 1


def test_scheduler_caps_interval_and_rejects_naive_clock():
    mature = reviewed_state(interval=300 * DAY, ease=3.0)
    assert schedule_review(mature, "easy", NOW).interval_seconds == MAX_INTERVAL_SECONDS
    with pytest.raises(ValueError, match="timezone-aware"):
        schedule_review(mature, "good", NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    "updates",
    [
        {"review_count": -1},
        {"review_count": 1},
        {"last_reviewed_at": NOW},
        {"ease_factor": 1.2},
        {"due_at": NOW.replace(tzinfo=None)},
    ],
)
def test_review_state_invariants(updates):
    payload = ReviewState.new(NOW).model_dump()
    payload.update(updates)
    with pytest.raises(ValidationError):
        ReviewState.model_validate(payload)


def set_review(value, item_id, review):
    items = value._read_items()
    items = [item.model_copy(update={"review": review}) if item.id == item_id else item for item in items]
    value._write_items(items)


def test_summary_counts_due_new_active_filters_and_excludes_archived(tmp_path):
    value = store(tmp_path)
    word, chunk, german, archived = value.save_items([
        study_input(), study_input("chunk"),
        study_input(text="Reise", lemma=None, source_language="de"),
        study_input(text="other", lemma=None),
    ])[0]
    set_review(value, word.id, reviewed_state(due_at=NOW))
    set_review(value, chunk.id, reviewed_state(due_at=NOW + timedelta(hours=1)))
    value.update_item("learner-one", archived.id, StudyItemPatch(active=False))
    service = ReviewService(value, clock=lambda: NOW)

    assert service.summary("learner-one").model_dump() == {
        "due_words": 1, "due_chunks": 0, "new_words": 1, "new_chunks": 0,
        "active_words": 2, "active_chunks": 1,
    }
    german_summary = service.summary("learner-one", language="de")
    assert german_summary.new_words == 1 and german_summary.active_words == 1


def test_queue_prioritizes_due_then_new_with_stable_limits_and_no_write(tmp_path):
    value = store(tmp_path)
    inputs = [study_input(text=f"word {index}", lemma=None) for index in range(15)]
    items = value.save_items(inputs)[0]
    set_review(value, items[2].id, reviewed_state(due_at=NOW - timedelta(days=2)))
    set_review(value, items[1].id, reviewed_state(due_at=NOW - timedelta(days=1)))
    before = value.path.read_text()
    queue = ReviewService(value, clock=lambda: NOW).queue("learner-one", limit=5, new_limit=2)
    assert [item.id for item in queue[:2]] == [items[2].id, items[1].id]
    assert len(queue) == 4
    assert [item.id for item in queue[2:]] == [items[0].id, items[3].id]
    assert value.path.read_text() == before


def test_queue_filters_kind_language_and_uses_no_new_when_due_fills_limit(tmp_path):
    value = store(tmp_path)
    word, chunk, german = value.save_items([
        study_input(), study_input("chunk"), study_input(text="Reise", lemma=None, source_language="de"),
    ])[0]
    set_review(value, word.id, reviewed_state())
    set_review(value, chunk.id, reviewed_state())
    service = ReviewService(value, clock=lambda: NOW)
    assert [item.id for item in service.queue("learner-one", kind="chunk", limit=1)] == [chunk.id]
    assert [item.id for item in service.queue("learner-one", language="de")] == [german.id]


@pytest.fixture
def review_api_store(tmp_path, monkeypatch):
    value = store(tmp_path)
    monkeypatch.setattr(study_bank_api, "study_bank_store", value)
    return value


def test_review_api_summary_queue_and_rating_contract(review_api_store):
    item = review_api_store.save_items([study_input()])[0][0]
    summary = client.get("/api/review/summary", params={"user_id": "learner-one"})
    queue = client.get("/api/review/queue", params={"user_id": "learner-one"})
    rated = client.post(f"/api/review/items/{item.id}/rate", json={
        "user_id": "learner-one", "rating": "good", "expected_review_count": 0,
    })
    assert summary.status_code == queue.status_code == rated.status_code == 200
    assert summary.json()["new_words"] == 1
    assert queue.json()["items"][0]["id"] == item.id
    assert rated.json()["previous_due_at"] == NOW.isoformat().replace("+00:00", "Z")
    assert rated.json()["interval_seconds"] == DAY
    assert rated.json()["item"]["review"]["review_count"] == 1


def test_stale_double_submit_archived_and_cross_user_ratings_are_rejected(review_api_store):
    item = review_api_store.save_items([study_input()])[0][0]
    payload = {"user_id": "learner-one", "rating": "good", "expected_review_count": 0}
    assert client.post(f"/api/review/items/{item.id}/rate", json=payload).status_code == 200
    assert client.post(f"/api/review/items/{item.id}/rate", json=payload).status_code == 409
    review_api_store.update_item("learner-one", item.id, StudyItemPatch(active=False))
    payload["expected_review_count"] = 1
    assert client.post(f"/api/review/items/{item.id}/rate", json=payload).status_code == 409
    payload["user_id"] = "other"
    assert client.post(f"/api/review/items/{item.id}/rate", json=payload).status_code == 404


def test_daily_review_rating_does_not_change_chunk_memory_statistics(
    review_api_store, tmp_path, monkeypatch,
):
    item = review_api_store.save_items([study_input("chunk")])[0][0]
    memory = ChunkMemoryService(
        ChunkStore(tmp_path / "chunk_memory.json"),
        clock=lambda: NOW,
        id_factory=lambda: "memory-one",
    )
    chunk, _ = memory.save_chunk(
        user_id="learner-one",
        text=item.text,
        language="en",
        meaning=item.meaning,
    )
    memory.record_practice("learner-one", chunk.id, "success")
    before = memory.list_chunks("learner-one")[0]
    monkeypatch.setattr(chunk_api, "chunk_memory_service", memory)

    response = client.post(f"/api/review/items/{item.id}/rate", json={
        "user_id": "learner-one", "rating": "again", "expected_review_count": 0,
    })

    after = memory.list_chunks("learner-one")[0]
    assert response.status_code == 200
    assert after == before


def test_rating_is_atomic_and_process_local_serialized(tmp_path, monkeypatch):
    value = store(tmp_path)
    item = value.save_items([study_input()])[0][0]
    calls = []
    real_replace = os.replace
    monkeypatch.setattr("services.study_bank.store.os.replace", lambda source, destination: (calls.append(1), real_replace(source, destination))[1])

    def rate():
        try:
            value.rate_item("learner-one", item.id, "good", 0)
            return "ok"
        except Exception as exc:
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: rate(), range(2)))
    assert results.count("ok") == 1
    assert value.get_item("learner-one", item.id).review.review_count == 1
    assert calls.count(1) == 1
