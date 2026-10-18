import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.api import chunk_api, library_api, study_bank_api
from services.library.store import LearningLibraryStore
from services.retrieval.chunk_memory import ChunkMemoryService
from services.retrieval.chunk_store import ChunkStore
from services.schemas.lesson import Lesson
from services.schemas.study_bank import StudyItemInput, StudyItemPatch, StudyProvenance
from services.study_bank.candidates import generate_candidates
from services.study_bank.store import StudyBankStore, StudyBankStoreError


client = TestClient(app_module.app)
NOW = datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


def provenance(**updates):
    values = {
        "library_item_id": "library-one",
        "lesson_id": "lesson-one",
        "source_url": "https://www.youtube.com/watch?v=source_one",
        "media_title": "Useful media",
        "sentence_id": 1,
        "sentence_text": "Los geht's, we can figure it out.",
        "sentence_translation": "出发吧，我们能解决。",
        "start": 18.0,
        "end": 21.0,
    }
    values.update(updates)
    return StudyProvenance(**values)


def study_input(kind="word", **updates):
    values = {
        "user_id": "learner-one",
        "kind": kind,
        "source_language": "en",
        "target_language": "zh-CN",
        "text": "figuring",
        "meaning": "解决",
        "lemma": "figure",
        "part_of_speech": "verb",
        "example": "We can figure it out.",
        "provenance": [provenance()],
    }
    if kind == "chunk":
        values.update({
            "text": "figure it out",
            "lemma": None,
            "part_of_speech": None,
            "example": None,
            "usage": "solve or understand something",
        })
    values.update(updates)
    return StudyItemInput(**values)


def bank(tmp_path, *, times=None):
    identifiers = iter(f"study-{index}" for index in range(1, 100))
    clock = (lambda: next(times)) if times is not None else (lambda: NOW)
    return StudyBankStore(
        tmp_path / "study_bank.json",
        clock=clock,
        id_factory=lambda: next(identifiers),
    )


def lesson(**updates):
    payload = {
        "id": "lesson-one",
        "title": "Useful media",
        "source_url": "https://www.youtube.com/watch?v=source_one",
        "source_language": "en",
        "target_language": "zh-CN",
        "level": "B2",
        "audio_url": None,
        "source": "subtitles",
        "sentences": [{
            "id": 1,
            "text": "Los geht's, we can figure it out.",
            "translation": "出发吧，我们能解决。",
            "start": 18.0,
            "end": 21.0,
            "keywords": [
                {"word": "figuring", "lemma": "figure", "meaning": "解决", "pos": "verb", "example": "We can figure it out."},
                {"word": "[music]", "meaning": ""},
            ],
            "chunks": [
                {"text": "figure it out", "meaning": "解决", "usage": "spoken problem solving"},
                {"text": "To go?", "meaning": "去？"},
                {"text": "Ah Ah Ah", "meaning": ""},
            ],
            "ai_status": "ok",
        }],
        "ai": {"provider": "mock", "status": "complete"},
    }
    payload.update(updates)
    return Lesson.model_validate(payload)


def library(tmp_path):
    return LearningLibraryStore(
        tmp_path / "learning_library.json",
        tmp_path / "library_media",
        tmp_path / "media",
        clock=lambda: NOW,
    )


def test_study_bank_starts_empty_and_persists_words_and_chunks(tmp_path):
    value = bank(tmp_path)
    assert value.list_items("learner-one") == []

    saved, created = value.save_items([study_input(), study_input("chunk")])

    assert created == 2
    assert [item.kind for item in saved] == ["word", "chunk"]
    assert StudyBankStore(value.path).list_items("learner-one", active=None) == list(reversed(saved))
    assert json.loads(value.path.read_text())["version"] == 1


def test_word_dedup_prefers_lemma_and_preserves_learner_fields(tmp_path):
    value = bank(tmp_path, times=iter([NOW, LATER]))
    first, _ = value.save_items([study_input(note="Keep this", meaning="learner meaning")])
    duplicate, created = value.save_items([study_input(text="FIGURED", meaning="weaker", note=None)])

    assert created == 0
    assert duplicate[0].id == first[0].id
    assert duplicate[0].created_at == NOW
    assert duplicate[0].meaning == "learner meaning"
    assert duplicate[0].note == "Keep this"


def test_chunk_dedup_uses_existing_safe_normalization(tmp_path):
    value = bank(tmp_path)
    first, _ = value.save_items([study_input("chunk")])
    duplicate, created = value.save_items([study_input("chunk", text=" FIGURE it out! ")])

    assert created == 0
    assert duplicate[0].id == first[0].id


def test_second_media_merges_provenance_without_duplicates(tmp_path):
    value = bank(tmp_path, times=iter([NOW, LATER, LATER]))
    first, _ = value.save_items([study_input("chunk")])
    second_source = provenance(
        library_item_id="library-two", lesson_id="lesson-two",
        media_title="Second media", sentence_id=4,
    )
    merged, _ = value.save_items([study_input("chunk", provenance=[second_source])])
    repeated, _ = value.save_items([study_input("chunk", provenance=[second_source])])

    assert merged[0].id == first[0].id
    assert len(merged[0].provenance) == 2
    assert len(repeated[0].provenance) == 2
    assert repeated[0].updated_at == LATER


def test_language_and_kind_are_separate_identities(tmp_path):
    value = bank(tmp_path)
    items, created = value.save_items([
        study_input(text="Start", lemma=None),
        study_input(text="Start", lemma=None, source_language="de"),
        study_input("chunk", text="Start", usage=None),
    ])

    assert created == 3
    assert len({item.id for item in items}) == 3


@pytest.mark.parametrize("text", ["", "?!", "[music]", "(SINGING)"])
def test_word_validation_rejects_malformed_or_caption_noise(tmp_path, text):
    value = bank(tmp_path)
    with pytest.raises(ValueError) as error:
        value.save_items([study_input(text=text, lemma=None)])
    assert error.value is not None


@pytest.mark.parametrize("text", ["[musik]", "[singen]"])
def test_chunk_validation_rejects_curation_specific_caption_noise(tmp_path, text):
    value = bank(tmp_path)
    with pytest.raises(ValueError, match="reusable spoken language"):
        value.save_items([study_input("chunk", text=text)])


@pytest.mark.parametrize(
    "payload,message",
    [("{broken", "Malformed"), (json.dumps({"version": 9, "items": []}), "Unsupported")],
)
def test_invalid_store_is_not_overwritten(tmp_path, payload, message):
    value = bank(tmp_path)
    value.path.write_text(payload, encoding="utf-8")

    with pytest.raises(StudyBankStoreError, match=message):
        value.save_items([study_input()])

    assert value.path.read_text(encoding="utf-8") == payload


def test_atomic_write_and_process_local_concurrency(tmp_path, monkeypatch):
    value = bank(tmp_path)
    calls = []
    real_replace = os.replace

    def replace(source, destination):
        calls.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr("services.study_bank.store.os.replace", replace)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: value.save_items([study_input()]), range(20)))

    assert len(value.list_items("learner-one")) == 1
    assert str(calls[-1][0]).endswith(".tmp")
    assert calls[-1][1] == value.path


def test_archive_restore_edit_and_delete(tmp_path):
    value = bank(tmp_path, times=iter([NOW, LATER, LATER, LATER]))
    item = value.save_items([study_input()])[0][0]
    archived = value.update_item("learner-one", item.id, StudyItemPatch(active=False))
    assert archived.active is False
    assert value.list_items("learner-one") == []
    restored = value.update_item(
        "learner-one", item.id,
        StudyItemPatch(active=True, meaning="edited", note="learner note"),
    )
    assert restored.meaning == "edited" and restored.note == "learner note"
    value.delete_item("learner-one", item.id)
    assert value.list_items("learner-one", active=None) == []


def test_candidates_are_deterministic_classified_and_read_only(tmp_path):
    library_store = library(tmp_path)
    library_item = library_store.save_item(lesson())
    value = bank(tmp_path)
    before = value.path.read_text()

    first = generate_candidates(library_item, [])
    second = generate_candidates(library_item, [])

    assert [item.candidate_id for item in first] == [item.candidate_id for item in second]
    assert {(item.text, item.quality) for item in first} >= {
        ("figure it out", "recommended"),
        ("To go?", "review"),
        ("Ah Ah Ah", "rejected"),
        ("[music]", "rejected"),
    }
    assert value.path.read_text() == before


def test_useful_short_german_expression_is_recommended(tmp_path):
    german = lesson(
        source_language="de",
        sentences=[{
            "id": 1, "text": "Los geht's.", "translation": "出发吧。",
            "start": 0, "end": 1,
            "chunks": [{"text": "Los geht's.", "meaning": "出发吧"}],
            "ai_status": "ok",
        }],
    )
    library_item = library(tmp_path).save_item(german)

    candidate = generate_candidates(library_item, [])[0]

    assert candidate.quality == "recommended"


def test_duplicate_candidates_in_one_sentence_are_not_repeated(tmp_path):
    duplicate_lesson = lesson()
    duplicate_lesson.sentences[0].keywords.append(
        duplicate_lesson.sentences[0].keywords[0].model_copy()
    )
    duplicate_lesson.sentences[0].chunks.append(
        duplicate_lesson.sentences[0].chunks[0].model_copy()
    )
    library_item = library(tmp_path).save_item(duplicate_lesson)

    candidates = generate_candidates(library_item, [])

    assert [item.text for item in candidates].count("figuring") == 1
    assert [item.text for item in candidates].count("figure it out") == 1


def test_candidate_already_saved_state_and_provenance(tmp_path):
    library_item = library(tmp_path).save_item(lesson())
    value = bank(tmp_path)
    saved = value.save_items([study_input(
        "chunk",
        provenance=[provenance(library_item_id=library_item.id)],
    )])[0]

    candidate = next(
        item for item in generate_candidates(library_item, saved)
        if item.text == "figure it out"
    )

    assert candidate.already_saved is True
    assert candidate.study_item_id == saved[0].id
    assert candidate.provenance.start == 18.0
    assert candidate.provenance.sentence_text.startswith("Los geht's")


def test_existing_identity_from_another_media_remains_saveable_for_provenance_merge(tmp_path):
    library_item = library(tmp_path).save_item(lesson())
    value = bank(tmp_path)
    saved = value.save_items([study_input("chunk")])[0]

    candidate = next(
        item for item in generate_candidates(library_item, saved)
        if item.text == "figure it out"
    )

    assert candidate.already_saved is False
    assert candidate.study_item_id == saved[0].id


def test_library_deletion_does_not_delete_study_bank(tmp_path):
    library_store = library(tmp_path)
    library_item = library_store.save_item(lesson())
    value = bank(tmp_path)
    saved = value.save_items([
        study_input("chunk", provenance=[provenance(library_item_id=library_item.id)])
    ])[0][0]

    library_store.delete_item(library_item.id)

    assert value.get_item("learner-one", saved.id).provenance[0].media_title == "Useful media"


@pytest.fixture
def api_stores(tmp_path, monkeypatch):
    study = bank(tmp_path)
    lessons = library(tmp_path)
    memory = ChunkMemoryService(
        ChunkStore(tmp_path / "chunk_bank.json"),
        clock=lambda: NOW,
        id_factory=lambda: "memory-one",
    )
    monkeypatch.setattr(study_bank_api, "study_bank_store", study)
    monkeypatch.setattr(library_api, "library_store", lessons)
    monkeypatch.setattr(chunk_api, "chunk_memory_service", memory)
    return study, lessons, memory


def input_payload(kind="word", **updates):
    return study_input(kind, **updates).model_dump(mode="json")


def test_candidate_and_crud_api_bulk_is_idempotent(api_stores):
    study, lessons, _ = api_stores
    library_item = lessons.save_item(lesson())
    candidate_response = client.get(
        f"/api/library/items/{library_item.id}/study-candidates",
        params={"user_id": "learner-one"},
    )
    assert candidate_response.status_code == 200
    assert len(candidate_response.json()["candidates"]) == 5

    payload = {"items": [input_payload(), input_payload("chunk")]}
    first = client.post("/api/study-bank/items/bulk", json=payload)
    second = client.post("/api/study-bank/items/bulk", json=payload)
    listed = client.get("/api/study-bank/items", params={"user_id": "learner-one"})

    assert first.status_code == second.status_code == listed.status_code == 200
    assert first.json()["created"] == 2
    assert second.json()["created"] == 0
    assert len(listed.json()["items"]) == 2

    item_id = first.json()["items"][0]["id"]
    edited = client.patch(
        f"/api/study-bank/items/{item_id}",
        params={"user_id": "learner-one"},
        json={"meaning": "edited", "note": "mine", "active": False},
    )
    assert edited.json()["active"] is False
    assert edited.json()["note"] == "mine"
    deleted = client.delete(
        f"/api/study-bank/items/{item_id}", params={"user_id": "learner-one"}
    )
    assert deleted.status_code == 204


def test_curated_chunk_ensures_memory_and_preserves_statistics(api_stores):
    study, _, memory = api_stores
    existing, _ = memory.save_chunk(
        user_id="learner-one", text="figure it out", language="en", meaning="old"
    )
    memory.record_practice("learner-one", existing.id, "success")

    response = client.post(
        "/api/study-bank/items/bulk", json={"items": [input_payload("chunk")]}
    )

    assert response.status_code == 200
    stored = memory.list_chunks("learner-one")[0]
    assert stored.id == existing.id
    assert stored.practice_count == 1 and stored.success_count == 1
    assert study.active_chunk_keys("learner-one", "en") == {"figure it out"}


def test_invalid_bulk_chunk_does_not_write_either_store(api_stores):
    study, _, memory = api_stores
    response = client.post(
        "/api/study-bank/items/bulk",
        json={"items": [input_payload("chunk", text="[singen]")]},
    )

    assert response.status_code == 422
    assert study.list_items("learner-one", active=None) == []
    assert memory.list_chunks("learner-one", include_archived=True) == []


def test_curating_an_archived_memory_restores_it_without_losing_statistics(api_stores):
    _, _, memory = api_stores
    existing, _ = memory.save_chunk(
        user_id="learner-one", text="figure it out", language="en"
    )
    memory.record_practice("learner-one", existing.id, "failure")
    memory.set_active("learner-one", existing.id, False)

    response = client.post(
        "/api/study-bank/items/bulk", json={"items": [input_payload("chunk")]}
    )

    assert response.status_code == 200
    restored = memory.list_chunks("learner-one")[0]
    assert restored.id == existing.id and restored.active is True
    assert restored.practice_count == 1 and restored.failure_count == 1


def test_active_recall_uses_only_active_curated_chunks(api_stores):
    study, _, memory = api_stores
    curated, _ = memory.save_chunk(
        user_id="learner-one", text="figure it out", language="en"
    )
    memory.save_chunk(user_id="learner-one", text="legacy candidate", language="en")
    saved = study.save_items([study_input("chunk")])[0][0]

    selected = client.get(
        "/api/chunks/memory/active-recall",
        params={"user_id": "learner-one", "language": "en"},
    )
    assert [item["id"] for item in selected.json()["chunks"]] == [curated.id]

    study.update_item("learner-one", saved.id, StudyItemPatch(active=False))
    empty = client.get(
        "/api/chunks/memory/active-recall",
        params={"user_id": "learner-one", "language": "en"},
    )
    assert empty.json() == {"chunks": []}
    assert len(memory.list_chunks("learner-one")) == 2


def test_study_bank_membership_controls_recall_over_legacy_memory_active_flag(api_stores):
    study, _, memory = api_stores
    chunk, _ = memory.save_chunk(
        user_id="learner-one", text="figure it out", language="en"
    )
    study.save_items([study_input("chunk")])
    memory.set_active("learner-one", chunk.id, False)

    response = client.get(
        "/api/chunks/memory/active-recall",
        params={"user_id": "learner-one", "language": "en"},
    )

    assert [item["id"] for item in response.json()["chunks"]] == [chunk.id]


def test_deleting_study_item_preserves_chunk_practice_history(api_stores):
    study, _, memory = api_stores
    response = client.post(
        "/api/study-bank/items/bulk", json={"items": [input_payload("chunk")]}
    )
    study_item = response.json()["items"][0]
    chunk = memory.list_chunks("learner-one")[0]
    memory.record_practice("learner-one", chunk.id, "failure")

    client.delete(
        f"/api/study-bank/items/{study_item['id']}",
        params={"user_id": "learner-one"},
    )

    preserved = memory.list_chunks("learner-one")[0]
    assert preserved.practice_count == 1 and preserved.failure_count == 1


def test_candidate_endpoint_makes_no_network_call(api_stores, monkeypatch):
    _, lessons, _ = api_stores
    library_item = lessons.save_item(lesson())
    monkeypatch.setattr(
        "socket.create_connection",
        lambda *args, **kwargs: pytest.fail("network access attempted"),
    )

    response = client.get(
        f"/api/library/items/{library_item.id}/study-candidates",
        params={"user_id": "learner-one"},
    )

    assert response.status_code == 200
