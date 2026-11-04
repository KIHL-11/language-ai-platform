"""B-4D: accepting Cloud AI Chunk candidates (P8-03B2 transaction, reused as
is) lands in the owner's Study Bank and Daily Review without resetting SRS.
The existing P8-03B2 tests already cover atomicity, duplicate accept keeping
SRS/archive/meaning/note, and reject side effects; these add cross-lesson
provenance, Daily Review via the API, and owner isolation."""
from __future__ import annotations

from fastapi.testclient import TestClient

import app as app_module
from services.schemas.lesson import Lesson
from services.schemas.study_bank import StudyItemPatch
from test_cloud_ai_chunk_library import _library
from test_durable_ai_chunk_candidates import (  # noqa: F401  (fixture)
    OWNER_A,
    OWNER_B,
    _as_owner,
    _decision,
    _lesson,
    _service,
    cloud_candidates,
)


client = TestClient(app_module.app)


def _second_lesson() -> Lesson:
    value = _lesson().model_dump(mode="json")
    value.update({
        "id": "candidate-lesson-2",
        "title": "Second candidate source",
        "source_url": "https://www.youtube.com/watch?v=candidate_2",
        "sentences": [{**value["sentences"][0], "id": 1,
                       "text": "As far as I know, the shop opens at nine.",
                       "translation": "据我所知，商店九点开门。", "start": 5.0, "end": 8.0}],
    })
    return Lesson.model_validate(value)


def _accept_first(service, owner, item, key):
    batch = service.suggest(owner, _library(owner, item.id, key=f"{key}-request"))
    candidate = next(value for value in batch.candidates if value.text.casefold() == "as far as i know")
    request = _decision(candidate, key=f"{key}-accept")
    request = request.model_copy(update={"user_id": owner})
    return service.decide(owner, batch.batch_id, request)


def test_the_same_chunk_from_two_lessons_is_one_card_with_both_sources(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    first = library.create_for_owner(OWNER_A, _lesson())
    second = library.create_for_owner(OWNER_A, _second_lesson())

    one = _accept_first(service, OWNER_A, first, "b4-chunk-1")
    rated, _ = study.rate_item(OWNER_A, one.study_item.id, "good", 0)
    two = _accept_first(service, OWNER_A, second, "b4-chunk-2")

    assert one.created is True and two.created is False
    assert two.study_item.id == one.study_item.id
    assert [p.library_item_id for p in two.study_item.provenance] == [first.id, second.id]
    assert [p.sentence_text for p in two.study_item.provenance] == [
        first.lesson.sentences[0].text, second.lesson.sentences[0].text,
    ]
    assert two.study_item.review == rated.review
    assert len(study.list_items(OWNER_A, kind="chunk", active=None)) == 1


def test_an_archived_chunk_is_not_restored_by_a_later_accept(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    first = library.create_for_owner(OWNER_A, _lesson())
    second = library.create_for_owner(OWNER_A, _second_lesson())
    card = _accept_first(service, OWNER_A, first, "b4-archive-1").study_item
    study.update_item(OWNER_A, card.id, StudyItemPatch(active=False, expected_revision=card.revision))

    again = _accept_first(service, OWNER_A, second, "b4-archive-2")

    assert again.created is False and again.study_item.active is False
    assert len(again.study_item.provenance) == 2


def test_accepted_chunks_are_queued_and_rated_in_daily_review(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    item = library.create_for_owner(OWNER_A, _lesson())
    card = _accept_first(_service(clock, study, library, candidates), OWNER_A, item, "b4-review").study_item

    with _as_owner(OWNER_A, candidates, study, library):
        queue = client.get("/api/review/queue", params={"user_id": OWNER_A}).json()
        rated = client.post(
            f"/api/review/items/{card.id}/rate",
            json={"user_id": OWNER_A, "rating": "easy", "expected_review_count": 0},
        )

    assert card.id in [value["id"] for value in queue["items"]]
    assert rated.status_code == 200
    assert study.get_item(OWNER_A, card.id).review.review_count == 1


def test_accepting_writes_only_the_current_owners_study_bank(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    item_a = library.create_for_owner(OWNER_A, _lesson())
    item_b = library.create_for_owner(OWNER_B, _lesson())

    _accept_first(service, OWNER_A, item_a, "b4-owner-a")
    _accept_first(service, OWNER_B, item_b, "b4-owner-b")

    [card_a] = study.list_items(OWNER_A, kind="chunk", active=None)
    [card_b] = study.list_items(OWNER_B, kind="chunk", active=None)
    assert card_a.id != card_b.id
    assert [p.library_item_id for p in card_a.provenance] == [item_a.id]
    assert [p.library_item_id for p in card_b.provenance] == [item_b.id]
