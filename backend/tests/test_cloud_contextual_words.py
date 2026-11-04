"""B-4: Cloud Listening -> Dictionary -> Save Word -> Study Bank -> Daily Review.

Owner-scoped PostgreSQL Library and Study Bank, the synthetic cloud dictionary
and synthetic EN/DE lessons only.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import psycopg
import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.api import dictionary_api, library_api, study_bank_api
from services.auth.identity import get_request_identity
from services.dictionary.cloud import NoPronunciationProvider, SyntheticFixtureDictionaryProvider
from services.dictionary.service import DictionaryService
from test_owner_scoped_library import lesson
from test_owner_scoped_study_bank import (  # noqa: F401  (fixture)
    OWNER_A,
    OWNER_B,
    _as_owner,
    _identity,
    cloud_repositories,
)


client = TestClient(app_module.app)


class _Untouchable:
    def __getattr__(self, name):
        raise AssertionError(f"cloud request reached a Personal store ({name})")


@pytest.fixture
def cloud(cloud_repositories, monkeypatch):
    dsn, study, library = cloud_repositories
    app_module.app.state.cloud_dictionary_service = DictionaryService(
        SyntheticFixtureDictionaryProvider(), pronunciation_provider=NoPronunciationProvider()
    )
    # The Personal Library, Study Bank and dictionary must never be used.
    monkeypatch.setattr(library_api, "library_store", _Untouchable())
    monkeypatch.setattr(study_bank_api, "study_bank_store", _Untouchable())
    monkeypatch.setattr(dictionary_api, "dictionary_service", _Untouchable())
    yield dsn, study, library
    delattr(app_module.app.state, "cloud_dictionary_service")


def _sentence(sentence_id, text, translation, start):
    return {
        "id": sentence_id, "text": text, "translation": translation, "start": start, "end": start + 2.5,
        "keywords": [], "chunks": [], "grammar": "", "ai_status": "ok",
    }


def _english(library, owner=OWNER_A, url="https://www.youtube.com/watch?v=b4_en_1", sentences=None):
    return library.create_for_owner(owner, lesson(
        id=f"lesson-{url[-4:]}", title=f"English {url[-4:]}", source_url=url,
        sentences=sentences or [
            _sentence(1, "She ran to the station.", "她跑到车站。", 0.5),
            _sentence(2, "Tenants stay isolated.", "租户保持隔离。", 3.5),
        ],
    ))


def _german(library, owner=OWNER_A, url="https://www.youtube.com/watch?v=b4_de_1", text="Die Häuser sind alt."):
    return library.create_for_owner(owner, lesson(
        id=f"lesson-{url[-4:]}", title=f"Deutsch {url[-4:]}", source_url=url, source_language="de",
        sentences=[_sentence(1, text, "这些房子很旧。", 1.0)],
    ))


def _save(item, word, sentence_id=1, owner=OWNER_A, **extra):
    return client.post(
        f"/api/library/items/{item.id}/study-bank/words",
        json={"user_id": owner, "sentence_id": sentence_id, "word": word, **extra},
    )


def _status(item, word, sentence_id=1, owner=OWNER_A):
    return client.get(
        f"/api/library/items/{item.id}/study-bank/words/status",
        params={"user_id": owner, "sentence_id": sentence_id, "word": word},
    )


@contextmanager
def _owner(owner, study, library):
    with _as_owner(owner, study, library):
        yield


def test_english_word_is_saved_with_server_built_provenance(cloud):
    _, study, library = cloud
    item = _english(library)
    with _owner(OWNER_A, study, library):
        before = _status(item, "ran")
        saved = _save(item, "ran")
        after = _status(item, "ran")

    assert before.json() == {"status": "unsaved", "item": None}
    assert saved.status_code == 200 and saved.json()["created"] is True
    word = saved.json()["item"]
    assert (word["kind"], word["lemma"], word["part_of_speech"], word["text"]) == ("word", "run", "verb", "ran")
    assert "跑" in word["meaning"]
    assert word["provenance"] == [{
        "source_type": "media", "library_item_id": item.id, "lesson_id": item.lesson.id,
        "source_url": item.lesson.source_url, "media_title": item.lesson.title, "sentence_id": 1,
        "sentence_text": "She ran to the station.", "sentence_translation": "她跑到车站。",
        "start": 0.5, "end": 3.0,
    }]
    assert after.json()["status"] == "saved" and after.json()["item"]["id"] == word["id"]


def test_german_inflection_saves_under_its_lemma(cloud):
    _, study, library = cloud
    item = _german(library)
    with _owner(OWNER_A, study, library):
        saved = _save(item, "Häuser").json()["item"]

    assert (saved["lemma"], saved["source_language"]) == ("Haus", "de")
    assert "房子" in saved["meaning"]


def test_other_forms_and_lessons_reuse_the_card_and_merge_provenance(cloud):
    _, study, library = cloud
    first = _english(library)
    second = _english(library, url="https://www.youtube.com/watch?v=b4_en_2", sentences=[
        _sentence(1, "He runs every morning.", "他每天早上跑步。", 7.0),
    ])
    with _owner(OWNER_A, study, library):
        original = _save(first, "ran").json()["item"]
        other_form = _status(second, "runs").json()
        merged = _save(second, "runs").json()
        repeated = _save(second, "runs").json()

    assert other_form["status"] == "saved" and other_form["item"]["id"] == original["id"]
    assert merged["created"] is False and merged["item"]["id"] == original["id"]
    assert [p["library_item_id"] for p in merged["item"]["provenance"]] == [first.id, second.id]
    assert merged["item"]["text"] == "ran"  # the card keeps its first form
    # An identical source is never appended twice and does not bump the card.
    assert repeated["item"]["provenance"] == merged["item"]["provenance"]
    assert repeated["item"]["revision"] == merged["item"]["revision"]
    assert len(study.list_items(OWNER_A, kind="word", active=None)) == 1


def test_resaving_never_resets_review_state_or_notes(cloud):
    from services.schemas.study_bank import StudyItemPatch

    _, study, library = cloud
    item = _english(library)
    second = _english(library, url="https://www.youtube.com/watch?v=b4_en_3", sentences=[
        _sentence(1, "He runs every morning.", "他每天早上跑步。", 7.0),
    ])
    with _owner(OWNER_A, study, library):
        saved = _save(item, "ran").json()["item"]
        rated = client.post(
            f"/api/review/items/{saved['id']}/rate",
            json={"user_id": OWNER_A, "rating": "good", "expected_review_count": 0},
        )
        noted = study.update_item(OWNER_A, saved["id"], StudyItemPatch(
            note="my own note", expected_revision=study.get_item(OWNER_A, saved["id"]).revision,
        ))
        before = study.get_item(OWNER_A, saved["id"])
        _save(item, "ran")
        _save(second, "runs")
        after = study.get_item(OWNER_A, saved["id"])

    assert rated.status_code == 200 and noted.note == "my own note"
    assert after.review == before.review
    assert after.review.review_count == 1
    assert after.note == "my own note"


def test_archived_words_stay_archived_until_an_explicit_restore(cloud):
    _, study, library = cloud
    item = _english(library)
    other = _english(library, url="https://www.youtube.com/watch?v=b4_en_4", sentences=[
        _sentence(1, "He runs every morning.", "他每天早上跑步。", 7.0),
    ])
    with _owner(OWNER_A, study, library):
        saved = _save(item, "ran").json()["item"]
        archived = client.patch(
            f"/api/study-bank/items/{saved['id']}", params={"user_id": OWNER_A},
            json={"active": False, "expected_revision": saved["revision"]},
        ).json()
        resaved = _save(item, "ran").json()
        # A new source changes the card (provenance is merged), still no restore.
        merged = _save(other, "runs").json()
        status = _status(item, "ran").json()
        restored = client.patch(
            f"/api/study-bank/items/{saved['id']}", params={"user_id": OWNER_A},
            json={"active": True, "expected_revision": status["item"]["revision"]},
        )

    assert archived["active"] is False
    assert resaved["created"] is False and resaved["item"]["active"] is False
    assert merged["item"]["active"] is False and len(merged["item"]["provenance"]) == 2
    assert status["status"] == "archived"
    assert restored.status_code == 200 and restored.json()["active"] is True


def test_new_words_enter_daily_review_and_can_be_rated(cloud):
    _, study, library = cloud
    item = _english(library)
    with _owner(OWNER_A, study, library):
        saved = _save(item, "ran").json()["item"]
        queue = client.get("/api/review/queue", params={"user_id": OWNER_A}).json()
        rated = client.post(
            f"/api/review/items/{saved['id']}/rate",
            json={"user_id": OWNER_A, "rating": "good", "expected_review_count": 0},
        )
        stale = client.post(
            f"/api/review/items/{saved['id']}/rate",
            json={"user_id": OWNER_A, "rating": "good", "expected_review_count": 0},
        )

    assert saved["id"] in [value["id"] for value in queue["items"]]
    assert rated.status_code == 200
    assert stale.status_code == 409


def test_concurrent_saves_and_retries_make_exactly_one_card(cloud):
    _, study, library = cloud
    item = _english(library)
    barrier = threading.Barrier(8)

    def save(_):
        barrier.wait()
        return _save(item, "ran")

    # One identity override for the whole pool: the helpers mutate global
    # app state and must not be entered per thread (review L3).
    with _owner(OWNER_A, study, library):
        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(save, range(8)))

    assert {response.status_code for response in responses} == {200}
    assert sum(response.json()["created"] for response in responses) == 1
    assert len({response.json()["item"]["id"] for response in responses}) == 1
    [card] = study.list_items(OWNER_A, kind="word", active=None)
    assert len(card.provenance) == 1


def test_the_client_cannot_supply_lemma_meaning_owner_or_provenance(cloud):
    _, study, library = cloud
    item = _english(library)
    with _owner(OWNER_A, study, library):
        forged_fields = _save(item, "ran", lemma="fly", meaning="飞", provenance=[])
        forged_owner = _save(item, "ran", owner=OWNER_B)
        not_in_sentence = _save(item, "jumped")
        no_entry = _save(item, "station")
        bad_sentence = _save(item, "ran", sentence_id=9)

    assert forged_fields.status_code == 422
    assert forged_owner.status_code == 403
    assert not_in_sentence.status_code == 422
    assert no_entry.status_code == 422
    assert bad_sentence.status_code == 422
    assert study.list_items(OWNER_A, active=None) == []


def test_other_accounts_cannot_read_or_save_against_a_lesson(cloud):
    _, study, library = cloud
    item = _english(library)
    with _owner(OWNER_A, study, library):
        _save(item, "ran")
    with _owner(OWNER_B, study, library):
        status = _status(item, "ran", owner=OWNER_B)
        save = _save(item, "ran", owner=OWNER_B)
        own_bank = client.get("/api/study-bank/items", params={"user_id": OWNER_B, "active": "false"}).json()

    assert status.status_code == save.status_code == 404
    assert own_bank["items"] == []
    assert len(study.list_items(OWNER_B, active=None)) == 0


def test_row_level_security_hides_saved_words_from_other_owners(cloud):
    """Words saved through the cloud path are owner rows under RLS, checked
    with a NOSUPERUSER NOBYPASSRLS role (same pattern as P8-03B1)."""
    dsn, study, library = cloud
    with _owner(OWNER_A, study, library):
        _save(_english(library), "ran")
    with _owner(OWNER_B, study, library):
        _save(_german(library, owner=OWNER_B), "Häuser", owner=OWNER_B)

    def visible_to(owner):
        with psycopg.connect(dsn) as connection:
            connection.execute(
                """
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='cloud_study_rls_test') THEN
                        CREATE ROLE cloud_study_rls_test NOLOGIN NOSUPERUSER NOBYPASSRLS;
                    END IF;
                END $$
                """
            )
            connection.execute("GRANT USAGE ON SCHEMA public TO cloud_study_rls_test")
            connection.execute("GRANT SELECT ON cloud_study_items TO cloud_study_rls_test")
            connection.execute("SET ROLE cloud_study_rls_test")
            connection.execute("SELECT set_config('app.current_owner', %s, true)", (owner,))
            rows = connection.execute(
                "SELECT owner_user_id, lemma FROM cloud_study_items WHERE kind = 'word'"
            ).fetchall()
            connection.execute("RESET ROLE")
            return rows

    assert visible_to(OWNER_A) == [(OWNER_A, "run")]
    assert visible_to(OWNER_B) == [(OWNER_B, "Haus")]


def test_without_a_session_or_cloud_storage_it_fails_closed(cloud, monkeypatch):
    _, study, library = cloud
    item = _english(library)
    from services.auth.identity import DeploymentMode, RequestIdentity

    app_module.app.dependency_overrides[get_request_identity] = lambda: RequestIdentity(
        mode=DeploymentMode.CLOUD, principal=None
    )
    try:
        unauthenticated = _save(item, "ran")
    finally:
        app_module.app.dependency_overrides.pop(get_request_identity, None)
    with _owner(OWNER_A, study, library):
        delattr(app_module.app.state, "cloud_dictionary_service")
        monkeypatch.delenv("CLOUD_DICTIONARY_SOURCE", raising=False)
        no_dictionary = _save(item, "ran")
        app_module.app.state.cloud_dictionary_service = DictionaryService(SyntheticFixtureDictionaryProvider())
    app_module.app.dependency_overrides[get_request_identity] = lambda: _identity(OWNER_A)
    app_module.app.state.cloud_library_repository = library
    try:
        no_study_bank = _save(item, "ran")
    finally:
        app_module.app.dependency_overrides.pop(get_request_identity, None)
        delattr(app_module.app.state, "cloud_library_repository")

    assert unauthenticated.status_code == 401
    assert no_dictionary.status_code == 503
    assert no_dictionary.json()["detail"]["code"] == "cloud_dictionary_not_configured"
    assert no_study_bank.status_code == 503
    assert no_study_bank.json()["detail"]["code"] == "cloud_study_bank_storage_not_configured"


def test_the_six_cloud_to_personal_gates_stay_closed(cloud):
    _, study, library = cloud
    with _owner(OWNER_A, study, library):
        responses = [
            client.get("/api/chunks/memory", params={"user_id": OWNER_A}),
            client.get("/api/chunks/memory/active-recall", params={"user_id": OWNER_A, "language": "en"}),
            client.patch("/api/chunks/memory/c/practice", json={"user_id": OWNER_A, "outcome": "success"}),
            client.patch("/api/chunks/memory/c", json={"user_id": OWNER_A, "active": False}),
            client.get("/api/integration/target-languages", params={"user_id": OWNER_A, "source_language": "en"}),
            client.post("/api/integration/speaking/sessions/s/suggestions/0/save",
                        json={"user_id": OWNER_A, "target_language": "zh-CN"}),
            client.get("/api/library/items/x/study-candidates", params={"user_id": OWNER_A}),
        ]

    assert [response.status_code for response in responses] == [503] * 7


def test_the_owner_bound_study_bank_view_refuses_any_other_user(cloud):
    """Second layer behind the route's owner check: the view cannot be
    pointed at another account even if a caller passes its id."""
    from services.study_bank.contextual_words import OwnerStudyBankView

    _, study, _ = cloud
    view = OwnerStudyBankView(study, OWNER_A)

    with pytest.raises(ValueError):
        view.list_items(OWNER_B)
    assert view.list_items(OWNER_A) == []


def test_an_ambiguous_form_across_lemmas_is_refused_not_merged(cloud):
    """Review M1: "leaves" is leaf (noun) or leave (verb). Where the sentence
    cannot decide, cloud refuses to save rather than guess a card."""
    _, study, library = cloud
    item = _english(library, url="https://www.youtube.com/watch?v=b4_en_5", sentences=[
        _sentence(1, "The leaves fall in autumn.", "叶子在秋天落下。", 0.5),
        _sentence(2, "She leaves at nine.", "她九点离开。", 3.5),
        _sentence(3, "He leaves the office at six.", "他六点离开办公室。", 6.5),
    ])
    with _owner(OWNER_A, study, library):
        noun = _save(item, "leaves", sentence_id=1).json()["item"]
        ambiguous_status = _status(item, "leaves", sentence_id=2)
        ambiguous_save = _save(item, "leaves", sentence_id=2)
        verb = _save(item, "leaves", sentence_id=3).json()["item"]

    assert (noun["lemma"], verb["lemma"]) == ("leaf", "leave") and noun["id"] != verb["id"]
    assert ambiguous_status.status_code == ambiguous_save.status_code == 422
    assert "ambiguous_lemma" in ambiguous_save.json()["detail"]
    assert [len(card.provenance) for card in study.list_items(OWNER_A, kind="word", active=None)] == [1, 1]


def test_personal_m52_still_saves_ambiguous_lookups():
    """Cloud is stricter; the shared service keeps M5.2 behaviour in Personal."""
    from services.study_bank.contextual_words import ContextualWordService

    class Library:
        def get_item(self, _item_id):
            return library_item

    class Bank:
        def list_items(self, _user_id, active=None):
            return []

    from services.schemas.library import LibraryItem

    library_item = LibraryItem.model_validate({
        "id": "personal-item", "source_key": "k", "created_at": "2026-11-04T00:00:00Z",
        "updated_at": "2026-11-04T00:00:00Z",
        "lesson": lesson(sentences=[_sentence(1, "She leaves at nine.", "她九点离开。", 0.5)]).model_dump(mode="json"),
        "progress": {"training_order": lesson().training_order},
    })
    service = ContextualWordService(
        Library(), Bank(), DictionaryService(SyntheticFixtureDictionaryProvider())
    )

    value = service.build_input("personal-item", "local-learner", 1, "leaves")

    assert value.lemma in {"leaf", "leave"}


def test_status_points_at_the_card_a_save_would_write(cloud):
    """Review M2: identity includes the target language, like the upsert."""
    _, study, library = cloud
    chinese = _english(library)
    german_target = library.create_for_owner(OWNER_A, lesson(
        id="lesson-de-target", title="English for German speakers",
        source_url="https://www.youtube.com/watch?v=b4_en_de", target_language="de",
        sentences=[_sentence(1, "He runs every morning.", "Er läuft jeden Morgen.", 1.0)],
    ))
    with _owner(OWNER_A, study, library):
        zh_card = _save(chinese, "ran").json()["item"]
        status = _status(german_target, "runs").json()
        saved = _save(german_target, "runs").json()
        after = _status(german_target, "runs").json()

    assert status == {"status": "unsaved", "item": None}
    assert saved["created"] is True and saved["item"]["id"] != zh_card["id"]
    assert saved["item"]["target_language"] == "de"
    assert after["item"]["id"] == saved["item"]["id"]


def test_a_lesson_that_cannot_be_provenance_is_a_controlled_422(cloud):
    """Review L1: not the session middleware's misleading 503."""
    _, study, library = cloud
    item = library.create_for_owner(OWNER_A, lesson(
        title="T" * 1001, source_url="https://www.youtube.com/watch?v=b4_long",
        sentences=[_sentence(1, "She ran to the station.", "她跑到车站。", 0.5)],
    ))
    with _owner(OWNER_A, study, library):
        status = _status(item, "ran")
        save = _save(item, "ran")

    assert status.status_code == save.status_code == 422
    assert study.list_items(OWNER_A, active=None) == []


def test_a_card_at_the_provenance_cap_refuses_new_sources_cleanly(cloud):
    """Review L4: the 1,000-source cap is a controlled 422 and changes nothing;
    re-saving an existing source still succeeds."""
    dsn, study, library = cloud
    item = _english(library)
    other = _english(library, url="https://www.youtube.com/watch?v=b4_en_6", sentences=[
        _sentence(1, "He runs every morning.", "他每天早上跑步。", 7.0),
    ])
    with _owner(OWNER_A, study, library):
        card = _save(item, "ran").json()["item"]
    filler = [{"source_type": "ai_text", "source_text": f"run {index}.", "evidence_start": 0, "evidence_end": 3}
              for index in range(999)]
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "UPDATE cloud_study_items SET provenance_json = provenance_json || %s::jsonb WHERE item_id = %s",
            (json.dumps(filler), card["id"]),
        )
    before = study.get_item(OWNER_A, card["id"])

    with _owner(OWNER_A, study, library):
        same_source = _save(item, "ran")
        new_source = _save(other, "runs")

    assert len(before.provenance) == 1000
    assert same_source.status_code == 200
    assert new_source.status_code == 422
    assert study.get_item(OWNER_A, card["id"]).revision == before.revision
