from datetime import datetime, timedelta, timezone

import pytest

from services.retrieval.chunk_memory import ChunkMemoryService
from services.retrieval.chunk_store import ChunkStore
from services.schemas.retrieval import StoredChunk


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 10, 14, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


@pytest.fixture
def memory(tmp_path):
    clock = MutableClock()
    identifiers = iter(f"chunk-{index}" for index in range(1, 100))
    store = ChunkStore(tmp_path / "chunk_bank.json")
    service = ChunkMemoryService(
        store,
        clock=clock,
        id_factory=lambda: next(identifiers),
    )
    return service, store, clock


def save(
    service,
    text="How about tomorrow?",
    *,
    user_id="user-1",
    language="en",
    lesson_id="lesson-1",
    meaning="明天怎么样？",
):
    return service.save_chunk(
        user_id=user_id,
        text=text,
        meaning=meaning,
        language=language,
        source_lesson_id=lesson_id,
        source_media_id="youtube:test",
        source_stage="active_recall",
    )


def test_save_chunk_persists_all_memory_fields(memory):
    service, store, _ = memory

    chunk, created = save(service)
    persisted = ChunkStore(store.path).read_chunks()[0]

    assert created is True
    assert persisted == chunk
    assert chunk.user_id == "user-1"
    assert chunk.normalized_text == "how about tomorrow"
    assert chunk.practice_count == chunk.success_count == chunk.failure_count == 0
    assert chunk.mastery_score == 0
    assert chunk.active is True
    assert chunk.created_at == chunk.updated_at


def test_upsert_prevents_normalized_duplicates_and_preserves_identity(memory):
    service, store, clock = memory
    first, _ = save(service, "  How   about tomorrow?! ")
    clock.advance(minutes=5)

    duplicate, created = save(
        service,
        "how about tomorrow.",
        lesson_id="lesson-2",
        meaning="更新后的含义",
    )

    assert created is False
    assert duplicate.id == first.id
    assert duplicate.created_at == first.created_at
    assert duplicate.updated_at == clock.value
    assert duplicate.meaning == "更新后的含义"
    assert len(store.read_chunks()) == 1


def test_same_text_is_isolated_by_user(memory):
    service, _, _ = memory

    first, _ = save(service, user_id="user-1")
    second, _ = save(service, user_id="user-2")

    assert first.id != second.id
    assert [chunk.id for chunk in service.list_chunks("user-1")] == [first.id]
    assert [chunk.id for chunk in service.list_chunks("user-2")] == [second.id]


def test_same_text_is_isolated_by_language_and_german_case_is_preserved(memory):
    service, _, _ = memory

    english, _ = save(service, "Morgen!", language="en")
    german_noun, _ = save(service, "Morgen!", language="de")
    german_adverb, _ = save(service, "morgen", language="de")

    assert english.normalized_text == "morgen"
    assert german_noun.normalized_text == "Morgen"
    assert german_adverb.normalized_text == "morgen"
    assert len(service.list_chunks("user-1")) == 3


def test_list_chunks_filters_language_and_archived_state(memory):
    service, _, _ = memory
    active, _ = save(service, "One", language="en")
    archived, _ = save(service, "Two", language="en")
    save(service, "Drei", language="de")
    service.set_active("user-1", archived.id, False)

    assert [chunk.id for chunk in service.list_chunks("user-1", "en")] == [
        active.id
    ]
    assert {
        chunk.id
        for chunk in service.list_chunks(
            "user-1", "en", include_archived=True
        )
    } == {active.id, archived.id}


def test_active_recall_prioritizes_low_mastery_failures_and_stale_chunks(memory):
    service, _, clock = memory
    mastered, _ = save(service, "Mastered", lesson_id="old-1")
    failed, _ = save(service, "Failed", lesson_id="old-2")
    stale, _ = save(service, "Stale", lesson_id="old-3")
    fresh, _ = save(service, "Fresh", lesson_id="old-4")

    for _ in range(8):
        service.record_practice("user-1", mastered.id, "success")
    service.record_practice("user-1", failed.id, "failure")
    service.record_practice("user-1", stale.id, "partial")
    clock.advance(days=10)
    service.record_practice("user-1", fresh.id, "partial")

    selected = service.select_for_active_recall(
        "user-1", "en", current_lesson_id="current", limit=4
    )

    assert [chunk.id for chunk in selected] == [
        failed.id,
        stale.id,
        fresh.id,
        mastered.id,
    ]


def test_practice_success_updates_counts_mastery_and_timestamp(memory):
    service, _, clock = memory
    chunk, _ = save(service)
    clock.advance(hours=1)

    updated = service.record_practice("user-1", chunk.id, "success")

    assert updated.practice_count == 1
    assert updated.success_count == 1
    assert updated.failure_count == 0
    assert updated.mastery_score == 0.1
    assert updated.last_practiced_at == clock.value
    assert updated.updated_at == clock.value


def test_practice_partial_and_failure_update_deterministically(memory):
    service, _, _ = memory
    chunk, _ = save(service)
    partial = service.record_practice("user-1", chunk.id, "partial")
    failed = service.record_practice("user-1", chunk.id, "failure")

    assert partial.practice_count == 1
    assert partial.success_count == partial.failure_count == 0
    assert partial.mastery_score == 0.02
    assert failed.practice_count == 2
    assert failed.success_count == 0
    assert failed.failure_count == 1
    assert failed.mastery_score == 0


def test_mastery_is_clamped_to_zero_and_one(memory):
    service, _, _ = memory
    chunk, _ = save(service)

    for _ in range(20):
        chunk = service.record_practice("user-1", chunk.id, "success")
    assert chunk.mastery_score == 1

    for _ in range(20):
        chunk = service.record_practice("user-1", chunk.id, "failure")
    assert chunk.mastery_score == 0
    assert chunk.practice_count == 40
    assert chunk.success_count == chunk.failure_count == 20


def test_archived_chunks_are_excluded_from_active_recall(memory):
    service, _, _ = memory
    active, _ = save(service, "Active")
    archived, _ = save(service, "Archived")
    service.set_active("user-1", archived.id, False)

    selected = service.select_for_active_recall("user-1", "en")

    assert [chunk.id for chunk in selected] == [active.id]


def test_active_recall_excludes_current_lesson_and_honors_limit(memory):
    service, _, _ = memory
    current, _ = save(service, "Current", lesson_id="lesson-current")
    prior_one, _ = save(service, "Prior one", lesson_id="lesson-old-1")
    prior_two, _ = save(service, "Prior two", lesson_id="lesson-old-2")

    selected = service.select_for_active_recall(
        "user-1",
        "en",
        current_lesson_id="lesson-current",
        limit=1,
    )

    assert current.id not in {chunk.id for chunk in selected}
    assert [chunk.id for chunk in selected] == [prior_one.id]
    assert prior_two.id != prior_one.id


def test_empty_memory_returns_empty_lists(memory):
    service, _, _ = memory

    assert service.list_chunks("missing-user") == []
    assert service.select_for_active_recall("missing-user", "en") == []


def test_chunk_id_cannot_be_used_across_users(memory):
    service, _, _ = memory
    chunk, _ = save(service, user_id="user-1")

    with pytest.raises(KeyError):
        service.record_practice("user-2", chunk.id, "success")
    with pytest.raises(KeyError):
        service.set_active("user-2", chunk.id, False)


def test_version_one_bank_is_read_and_upgraded_on_next_write(tmp_path):
    path = tmp_path / "chunk_bank.json"
    path.write_text(
        """{
  "version": 1,
  "chunks": [{
    "id": "old",
    "text": "  Sounds good. ",
    "meaning": "好主意。",
    "source_language": "en",
    "source_lesson_id": "old-lesson",
    "source_sentence_id": 1,
    "created_at": "2026-01-01T00:00:00Z"
  }]
}\n""",
        encoding="utf-8",
    )
    service = ChunkMemoryService(
        ChunkStore(path),
        clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
        id_factory=lambda: "new",
    )

    legacy = service.list_chunks("legacy")
    service.save_chunk(
        user_id="user-1", text="New chunk", language="en"
    )

    assert legacy[0].normalized_text == "sounds good"
    assert legacy[0].updated_at == legacy[0].created_at
    assert '"version": 2' in path.read_text(encoding="utf-8")
