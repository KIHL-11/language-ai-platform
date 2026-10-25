from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.api import dictionary_api, library_api, study_bank_api
from services.library.store import LearningLibraryStore
from services.schemas.dictionary import DictionaryLookupResponse
from services.schemas.lesson import Lesson
from services.schemas.study_bank import StudyItemPatch
from services.study_bank.store import StudyBankStore


NOW = datetime(2026, 10, 25, 8, 0, tzinfo=timezone.utc)
client = TestClient(app_module.app)


def lesson(*, source_url="https://example.com/one", language="en", sentences=None):
    return Lesson.model_validate({
        "id": f"lesson-{source_url.rsplit('/', 1)[-1]}",
        "title": "Authoritative media",
        "source_url": source_url,
        "source_language": language,
        "target_language": "zh-CN",
        "level": "B2",
        "source": "subtitles",
        "sentences": sentences or [
            {"id": 1, "text": "We went home.", "translation": "我们回家了。", "start": 1, "end": 2},
            {"id": 2, "text": "The children played.", "translation": "孩子们玩耍。", "start": 2, "end": 4},
        ],
        "ai": {"provider": "mock", "status": "complete"},
    })


def dictionary_result(word, language="en"):
    values = {
        "went": ("go", "verb", "去", "We went home."),
        "children": ("child", "noun", "孩子", "The children played."),
        "Häuser": ("Haus", "noun", "房屋", "Die Häuser sind groß."),
    }
    lemma, part_of_speech, chinese, example = values[word]
    cross = "de" if language == "en" else "en"
    return DictionaryLookupResponse.model_validate({
        "query": word,
        "surface_form": word,
        "lemma": lemma,
        "lemma_candidates": [lemma],
        "language": language,
        "part_of_speech": part_of_speech,
        "status": "ambiguous" if word == "children" else "found",
        "explanations": {
            "monolingual": {"language": language, "value": "source definition", "status": "available", "source": "fixture", "kind": "dictionary_definition"},
            "cross_language": {"language": cross, "value": None, "status": "unavailable", "kind": "dictionary_translation"},
            "chinese": {"language": "zh-CN", "value": chinese, "status": "available", "source": "fixture", "kind": "dictionary_translation"},
        },
        "context": {"source_sentence": example, "selection_method": "morphology"},
        "examples": [example],
    })


class FakeDictionary:
    def __init__(self):
        self.calls = []

    def lookup(self, language, word, sentence, **_kwargs):
        self.calls.append((language, word, sentence))
        if word == "missing":
            result = dictionary_result("went", language)
            return result.model_copy(update={"status": "not_found", "lemma": None})
        return dictionary_result(word, language)


@pytest.fixture
def stores(tmp_path, monkeypatch):
    bank = StudyBankStore(
        tmp_path / "study.json",
        clock=lambda: NOW,
        id_factory=(lambda values=iter(f"study-{i}" for i in range(20)): next(values)),
    )
    library = LearningLibraryStore(
        tmp_path / "library.json",
        tmp_path / "library_media",
        tmp_path / "media",
        clock=lambda: NOW,
    )
    dictionary = FakeDictionary()
    monkeypatch.setattr(study_bank_api, "study_bank_store", bank)
    monkeypatch.setattr(library_api, "library_store", library)
    monkeypatch.setattr(dictionary_api, "dictionary_service", dictionary)
    return bank, library, dictionary


def save(item_id, sentence_id, word, user_id="learner-one"):
    return client.post(
        f"/api/library/items/{item_id}/study-bank/words",
        json={"user_id": user_id, "sentence_id": sentence_id, "word": word},
    )


def test_contextual_save_uses_exact_non_current_sentence_and_authoritative_provenance(stores):
    bank, library, dictionary = stores
    item = library.save_item(lesson())

    response = save(item.id, 2, "children")

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] is True
    saved = payload["item"]
    assert (saved["text"], saved["lemma"], saved["part_of_speech"], saved["meaning"]) == (
        "children", "child", "noun", "孩子",
    )
    assert saved["example"] == "The children played."
    assert saved["target_language"] == "zh-CN"
    assert saved["provenance"] == [{
        "source_type": "media",
        "library_item_id": item.id,
        "lesson_id": item.lesson.id,
        "source_url": item.lesson.source_url,
        "media_title": item.lesson.title,
        "sentence_id": 2,
        "sentence_text": "The children played.",
        "sentence_translation": "孩子们玩耍。",
        "start": 2.0,
        "end": 4.0,
    }]
    assert dictionary.calls == [("en", "children", "The children played.")]
    assert bank.list_items("learner-one")[0].id == saved["id"]


@pytest.mark.parametrize("sentence_id,word,status", [
    (99, "went", 422),
    (1, "wenthome", 422),
    (1, "missing", 422),
])
def test_contextual_save_rejects_invalid_sentence_token_or_dictionary_entry(
    stores, sentence_id, word, status
):
    bank, library, _ = stores
    item = library.save_item(lesson())
    assert save(item.id, sentence_id, word).status_code == status
    assert bank.list_items("learner-one", active=None) == []


def test_contextual_save_rejects_duplicate_sentence_ids(stores):
    bank, library, _ = stores
    item = library.save_item(lesson(sentences=[
        {"id": 1, "text": "We went home.", "start": 0, "end": 1},
        {"id": 1, "text": "They went home.", "start": 1, "end": 2},
    ]))
    response = save(item.id, 1, "went")
    assert response.status_code == 422
    assert "exactly one" in response.json()["detail"]
    assert bank.list_items("learner-one", active=None) == []


def test_unicode_token_validation_and_german_dictionary_context(stores):
    _, library, dictionary = stores
    item = library.save_item(lesson(
        language="de",
        sentences=[{"id": 7, "text": "Die Häuser sind groß.", "translation": "房子很大。", "start": 4, "end": 6}],
    ))
    response = save(item.id, 7, "Häuser")
    assert response.status_code == 200
    assert response.json()["item"]["lemma"] == "Haus"
    assert dictionary.calls == [("de", "Häuser", "Die Häuser sind groß.")]


def test_inflected_forms_deduplicate_and_merge_cross_media_provenance(stores):
    bank, library, _ = stores
    first = library.save_item(lesson(source_url="https://example.com/first"))
    second = library.save_item(lesson(
        source_url="https://example.com/second",
        sentences=[{"id": 8, "text": "We go now.", "translation": "我们现在走。", "start": 8, "end": 9}],
    ))

    assert save(first.id, 1, "went").json()["created"] is True
    # Make the fake source-grounded lookup resolve the other surface to the same lemma.
    original = dictionary_result
    second_response = dictionary_result("went").model_copy(update={"query": "go", "surface_form": "go"})
    class GoDictionary(FakeDictionary):
        def lookup(self, language, word, sentence, **kwargs):
            return second_response if word == "go" else original(word, language)
    dictionary_api.dictionary_service = GoDictionary()
    response = save(second.id, 8, "go")

    assert response.status_code == 200 and response.json()["created"] is False
    items = bank.list_items("learner-one", active=None)
    assert len(items) == 1
    assert {source.library_item_id for source in items[0].provenance} == {first.id, second.id}


def test_repeat_save_preserves_user_fields_review_and_archive_state(stores):
    bank, library, _ = stores
    item = library.save_item(lesson())
    first = save(item.id, 2, "children").json()["item"]
    bank.update_item("learner-one", first["id"], StudyItemPatch(meaning="我的释义", note="mine"))
    rated, _ = bank.rate_item("learner-one", first["id"], "good", 0)
    archived = bank.update_item("learner-one", first["id"], StudyItemPatch(active=False))

    repeated = save(item.id, 2, "children")

    assert repeated.status_code == 200 and repeated.json()["created"] is False
    saved = bank.get_item("learner-one", first["id"])
    assert saved.active is False
    assert (saved.meaning, saved.note) == ("我的释义", "mine")
    assert saved.review == rated.review == archived.review
    assert len(saved.provenance) == 1

    status = client.get(
        f"/api/library/items/{item.id}/study-bank/words/status",
        params={"user_id": "learner-one", "sentence_id": 2, "word": "children"},
    )
    assert status.status_code == 200
    assert status.json()["status"] == "archived"

    restored = client.patch(
        f"/api/study-bank/items/{first['id']}",
        params={"user_id": "learner-one"},
        json={"active": True},
    )
    assert restored.status_code == 200
    assert restored.json()["review"] == rated.model_dump(mode="json")["review"]


def test_new_word_is_in_daily_review_and_does_not_touch_chunk_memory(stores, monkeypatch):
    _, library, _ = stores
    item = library.save_item(lesson())
    monkeypatch.setattr(
        "services.api.chunk_api.chunk_memory_service.save_chunk",
        lambda *args, **kwargs: pytest.fail("Chunk Memory write attempted"),
    )
    assert save(item.id, 1, "went").status_code == 200

    summary = client.get("/api/review/summary", params={"user_id": "learner-one"})
    queue = client.get("/api/review/queue", params={"user_id": "learner-one"})
    assert summary.json()["new_words"] == 1
    assert [value["lemma"] for value in queue.json()["items"]] == ["go"]


def test_contextual_endpoints_are_controlled_and_make_no_network_calls(stores, monkeypatch):
    bank, library, _ = stores
    item = library.save_item(lesson())
    monkeypatch.setattr(
        "socket.create_connection",
        lambda *args, **kwargs: pytest.fail("network access attempted"),
    )
    assert save("missing-library", 1, "went").status_code == 404
    assert save(item.id, 1, "went", user_id="").status_code == 422
    assert save(item.id, 1, "went").status_code == 200
    assert bank.list_items("learner-one")[0].lemma == "go"


def test_dictionary_language_mismatch_and_storage_failure_do_not_create_records(
    stores, monkeypatch
):
    bank, library, _ = stores
    item = library.save_item(lesson())

    class WrongLanguageDictionary(FakeDictionary):
        def lookup(self, language, word, sentence, **kwargs):
            return dictionary_result(word, "de")

    monkeypatch.setattr(dictionary_api, "dictionary_service", WrongLanguageDictionary())
    mismatch = save(item.id, 1, "went")
    assert mismatch.status_code == 422
    assert "language does not match" in mismatch.json()["detail"]
    assert bank.list_items("learner-one", active=None) == []

    monkeypatch.setattr(dictionary_api, "dictionary_service", FakeDictionary())
    bank.path.write_text("{malformed", encoding="utf-8")
    failed = save(item.id, 1, "went")
    assert failed.status_code == 500
    assert failed.json() == {"detail": "Could not read or update Study Bank"}
    assert str(bank.path) not in failed.text
