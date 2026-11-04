"""B-3B: Cloud AI Chunk candidates from the owner's Cloud Library (EN/DE),
with save-only edits. Reuses the P8-03B2 durable candidate system; accepting
still saves to the Study Bank exactly as before (owner decision)."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.ai_chunks.service import AiChunkOrganizerService
from services.ai_chunks.store import CandidateConflictError, CandidateNotFoundError
from services.schemas.ai_chunks import CandidateDecisionRequest, ChunkSuggestionRequest
from services.schemas.lesson import Lesson
from test_durable_ai_chunk_candidates import (  # noqa: F401  (fixture)
    OWNER_A,
    OWNER_B,
    CountingMockAdapter,
    _as_owner,
    _decision,
    _lesson,
    _service,
    cloud_candidates,
)


client = TestClient(app_module.app)
EDIT = {
    "text": "as far as I know",
    "suggested_meaning_zh": "据我所知（已编辑）",
    "usage_note": "口语中引出个人判断",
    "example": "As far as I know, the shop is open.",
}


def _german_lesson() -> Lesson:
    value = _lesson().model_dump(mode="json")
    value.update({
        "id": "candidate-lesson-de",
        "source_url": "https://www.youtube.com/watch?v=candidate_de",
        "source_language": "de",
        "sentences": [{
            **value["sentences"][0],
            "text": "Auf jeden Fall kommt es darauf an.",
            "translation": "无论如何，这要看情况。",
        }],
    })
    return Lesson.model_validate(value)


def _library(owner, item_id, language="en", key="b3-library-request-01"):
    return ChunkSuggestionRequest.model_validate({
        "user_id": owner,
        "source_language": language,
        "source": {"source_type": "library_passage", "library_item_id": item_id, "sentence_ids": [1]},
        "idempotency_key": key,
    })


def _edit(candidate, *, key="b3-edit-decision-01", edit=None, owner=OWNER_A):
    return CandidateDecisionRequest.model_validate({
        "user_id": owner,
        "candidate_id": candidate.candidate_id,
        "action": "edit",
        "expected_version": candidate.version,
        "idempotency_key": key,
        "edit": edit or EDIT,
    })


@pytest.mark.parametrize(
    "language,lesson_factory,expected_text",
    [("en", _lesson, "as far as I know"), ("de", _german_lesson, "auf jeden fall")],
)
def test_library_lesson_generates_candidates_with_full_provenance(
    cloud_candidates, language, lesson_factory, expected_text
):
    _, clock, study, library, candidates = cloud_candidates
    adapter = CountingMockAdapter()
    item = library.create_for_owner(OWNER_A, lesson_factory())
    batch = _service(clock, study, library, candidates, adapter).suggest(
        OWNER_A, _library(OWNER_A, item.id, language)
    )

    candidate = next(value for value in batch.candidates if value.text.casefold() == expected_text.casefold())
    evidence = candidate.evidence
    assert batch.source_type == "library_passage"
    assert (candidate.source_language, candidate.state, candidate.version) == (language, "proposed", 1)
    assert evidence.source_type == "library_sentence"
    assert evidence.library_item_id == item.id
    assert evidence.lesson_id == lesson_factory().id
    assert (evidence.sentence_id, evidence.start, evidence.end) == (1, 1.0, 4.0)
    assert evidence.source_url == lesson_factory().source_url
    assert evidence.quote == lesson_factory().sentences[0].text
    assert evidence.quote[evidence.evidence_start:evidence.evidence_end].casefold() == expected_text.casefold()
    assert adapter.calls == 1
    assert study.list_items(OWNER_A, active=None) == []


def test_edit_saves_changes_keeps_the_candidate_proposed_and_writes_no_study_item(
    cloud_candidates,
):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    item = library.create_for_owner(OWNER_A, _lesson())
    batch = service.suggest(OWNER_A, _library(OWNER_A, item.id))
    original = batch.candidates[0]

    response = service.decide(OWNER_A, batch.batch_id, _edit(original))

    edited = response.candidate
    assert (edited.state, edited.version) == ("proposed", 2)
    assert (edited.text, edited.suggested_meaning_zh, edited.usage_note, edited.example) == (
        EDIT["text"], EDIT["suggested_meaning_zh"], EDIT["usage_note"], EDIT["example"]
    )
    assert response.study_item is None and edited.accepted_study_item_id is None
    for field in ("source_type", "quote", "source_label", "library_item_id", "lesson_id",
                  "sentence_id", "source_url", "sentence_translation", "start", "end"):
        assert getattr(edited.evidence, field) == getattr(original.evidence, field)
    assert study.list_items(OWNER_A, active=None) == []
    # Durable: a fresh read sees the edit.
    assert service.get_batch(OWNER_A, batch.batch_id).candidates[0] == edited


def test_an_edited_candidate_can_still_be_accepted_or_rejected(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    item = library.create_for_owner(OWNER_A, _lesson())
    batch = service.suggest(OWNER_A, _library(OWNER_A, item.id))
    edited = service.decide(OWNER_A, batch.batch_id, _edit(batch.candidates[0])).candidate

    accepted = service.decide(
        OWNER_A, batch.batch_id, _decision(edited, key="b3-accept-after-edit")
    )

    # Owner decision (2026-11-04): accepting keeps the existing P8-03B2
    # behaviour and saves to the Study Bank, with the edited content.
    assert accepted.candidate.state == "accepted"
    assert accepted.study_item.meaning == EDIT["suggested_meaning_zh"]
    assert [value.text for value in study.list_items(OWNER_A, active=None)] == [accepted.study_item.text]


def test_edits_must_stay_grounded_in_the_source_sentence(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    item = library.create_for_owner(OWNER_A, _lesson())
    batch = service.suggest(OWNER_A, _library(OWNER_A, item.id))

    with pytest.raises(ValueError):
        service.decide(OWNER_A, batch.batch_id, _edit(batch.candidates[0], edit={**EDIT, "text": "invented phrase"}))
    assert service.get_batch(OWNER_A, batch.batch_id).candidates[0].version == 1


def test_stale_edit_conflicts_and_a_replay_returns_the_same_result(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    item = library.create_for_owner(OWNER_A, _lesson())
    batch = service.suggest(OWNER_A, _library(OWNER_A, item.id))
    original = batch.candidates[0]
    first = service.decide(OWNER_A, batch.batch_id, _edit(original))

    replay = service.decide(OWNER_A, batch.batch_id, _edit(original))
    with pytest.raises(CandidateConflictError):
        service.decide(OWNER_A, batch.batch_id, _edit(original, key="b3-edit-decision-02"))
    with pytest.raises(CandidateConflictError):
        service.decide(OWNER_A, batch.batch_id, _edit(original, edit={**EDIT, "usage_note": "x"}))

    assert replay == first
    assert service.get_batch(OWNER_A, batch.batch_id).candidates[0].version == 2


def test_concurrent_edits_of_one_version_have_exactly_one_winner(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    item = library.create_for_owner(OWNER_A, _lesson())
    batch = service.suggest(OWNER_A, _library(OWNER_A, item.id))
    candidate = batch.candidates[0]
    barrier = threading.Barrier(4)

    def edit(index):
        barrier.wait()
        try:
            service.decide(OWNER_A, batch.batch_id, _edit(
                candidate, key=f"b3-concurrent-edit-{index:02d}",
                edit={**EDIT, "usage_note": f"note {index}"},
            ))
            return "ok"
        except CandidateConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(edit, range(4)))

    assert sorted(outcomes) == ["conflict", "conflict", "conflict", "ok"]
    assert service.get_batch(OWNER_A, batch.batch_id).candidates[0].version == 2


def test_repeated_generation_request_reuses_the_batch(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    adapter = CountingMockAdapter()
    service = _service(clock, study, library, candidates, adapter)
    item = library.create_for_owner(OWNER_A, _lesson())

    def run(_):
        # Same contract as manual text: a duplicate either replays the batch
        # or sees "generation in progress"; the mock runs once.
        try:
            return service.suggest(OWNER_A, _library(OWNER_A, item.id))
        except CandidateConflictError:
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        batches = list(executor.map(run, range(4)))
    retried = service.suggest(OWNER_A, _library(OWNER_A, item.id))

    assert {batch.batch_id for batch in batches if batch is not None} == {retried.batch_id}
    assert adapter.calls == 1


def test_other_accounts_cannot_generate_from_read_or_edit_a_library_batch(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    item = library.create_for_owner(OWNER_A, _lesson())
    batch = service.suggest(OWNER_A, _library(OWNER_A, item.id))

    with pytest.raises(KeyError):
        service.suggest(OWNER_B, _library(OWNER_B, item.id, key="b3-library-request-02"))
    with pytest.raises(CandidateNotFoundError):
        service.get_batch(OWNER_B, batch.batch_id)
    with pytest.raises(CandidateNotFoundError):
        service.decide(OWNER_B, batch.batch_id, _edit(batch.candidates[0], owner=OWNER_B))
    assert service.get_batch(OWNER_A, batch.batch_id).candidates[0].version == 1


def test_cloud_api_edit_uses_the_session_identity(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    item = library.create_for_owner(OWNER_A, _lesson())
    with _as_owner(OWNER_A, candidates, study, library):
        created = client.post("/api/ai/chunk-suggestions", json=_library(OWNER_A, item.id).model_dump(mode="json"))
        batch = created.json()
        candidate = batch["candidates"][0]
        edited = client.post(
            f"/api/ai/chunk-suggestions/{batch['batch_id']}/decisions",
            json={"user_id": OWNER_A, "candidate_id": candidate["candidate_id"], "action": "edit",
                  "expected_version": 1, "idempotency_key": "b3-api-edit-0001", "edit": EDIT},
        )
        forged = client.post(
            f"/api/ai/chunk-suggestions/{batch['batch_id']}/decisions",
            json={"user_id": OWNER_B, "candidate_id": candidate["candidate_id"], "action": "edit",
                  "expected_version": 2, "idempotency_key": "b3-api-edit-0002", "edit": EDIT},
        )
    with _as_owner(OWNER_B, candidates, study, library):
        cross = client.get(f"/api/ai/chunk-suggestions/{batch['batch_id']}?user_id={OWNER_B}")

    assert created.status_code == 200 and edited.status_code == 200
    assert (edited.json()["candidate"]["state"], edited.json()["study_item"]) == ("proposed", None)
    assert forged.status_code == 403
    assert cross.status_code == 404


def test_edit_requires_edit_content():
    with pytest.raises(ValueError):
        CandidateDecisionRequest.model_validate({
            "user_id": OWNER_A, "candidate_id": "c", "action": "edit",
            "expected_version": 1, "idempotency_key": "b3-edit-missing-01",
        })


def test_personal_desktop_refuses_save_only_edits(tmp_path):
    """Review M1: Personal keeps its accept/edit_and_accept/reject contract."""
    from services.ai_chunks.service import AiChunkValidationError
    from services.ai_chunks.store import InMemoryAiChunkStore
    from services.library.store import LearningLibraryStore
    from services.study_bank.store import StudyBankStore

    library = LearningLibraryStore(tmp_path / "library.json", tmp_path / "media", tmp_path / "source")
    study = StudyBankStore(tmp_path / "study.json")
    service = AiChunkOrganizerService(library, study, store=InMemoryAiChunkStore())
    request = ChunkSuggestionRequest.model_validate({
        "user_id": "local-learner", "source_language": "en",
        "source": {"source_type": "manual_text", "text": "As far as I know, it depends on the weather."},
        "idempotency_key": "b3-personal-request-01",
    })
    batch = service.suggest("local-learner", request)

    with pytest.raises(AiChunkValidationError):
        service.decide("local-learner", batch.batch_id, _edit(
            batch.candidates[0], owner="local-learner", key="b3-personal-edit-01"
        ))

    unchanged = service.get_batch("local-learner", batch.batch_id).candidates[0]
    assert (unchanged.state, unchanged.version) == ("proposed", 1)
    assert study.list_items("local-learner") == []


def test_saved_edits_are_capped_per_candidate(cloud_candidates):
    from services.ai_chunks.service import MAX_SAVED_EDITS, AiChunkValidationError

    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    item = library.create_for_owner(OWNER_A, _lesson())
    batch = service.suggest(OWNER_A, _library(OWNER_A, item.id))
    candidate = batch.candidates[0]
    for index in range(MAX_SAVED_EDITS):
        candidate = service.decide(OWNER_A, batch.batch_id, _edit(
            candidate, key=f"b3-cap-edit-{index:03d}", edit={**EDIT, "usage_note": f"note {index}"},
        )).candidate

    with pytest.raises(AiChunkValidationError):
        service.decide(OWNER_A, batch.batch_id, _edit(candidate, key="b3-cap-edit-over"))
    accepted = service.decide(OWNER_A, batch.batch_id, _decision(candidate, key="b3-cap-accept"))
    assert candidate.version == MAX_SAVED_EDITS + 1
    assert accepted.candidate.state == "accepted"


def test_a_saved_edit_stores_clean_text_and_clears_a_stale_study_bank_hint(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    item = library.create_for_owner(OWNER_A, _lesson())
    first = service.suggest(OWNER_A, _library(OWNER_A, item.id))
    service.decide(OWNER_A, first.batch_id, _decision(first.candidates[0], key="b3-hint-accept"))
    second = service.suggest(OWNER_A, _library(OWNER_A, item.id, key="b3-library-request-hint"))
    hinted = next(value for value in second.candidates if value.existing_study_item_id is not None)

    edited = service.decide(OWNER_A, second.batch_id, _edit(
        hinted, key="b3-hint-edit", edit={**EDIT, "text": "it  depends on"},
    )).candidate

    assert edited.text == "it depends on"
    assert (edited.existing_study_item_id, edited.existing_study_item_active) == (None, None)


def test_0011_rolls_back_only_without_edit_decisions(cloud_candidates):
    import psycopg

    from test_durable_ai_chunk_candidates import _migration

    dsn, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    item = library.create_for_owner(OWNER_A, _lesson())
    batch = service.suggest(OWNER_A, _library(OWNER_A, item.id))
    service.decide(OWNER_A, batch.batch_id, _edit(batch.candidates[0]))

    with pytest.raises(psycopg.errors.RaiseException):
        _migration(dsn, "0011_ai_chunk_edit_action.down.sql")
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM cloud_ai_chunk_decisions WHERE action = 'edit'"
        ).fetchone()[0] == 1
        connection.execute("DELETE FROM cloud_ai_chunk_decision_keys")
        connection.execute("DELETE FROM cloud_ai_chunk_decisions")
    _migration(dsn, "0011_ai_chunk_edit_action.down.sql")
    with psycopg.connect(dsn) as connection:
        definition = connection.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'cloud_ai_decision_action'"
        ).fetchone()[0]
        edit_guard = connection.execute(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'cloud_ai_decision_edit_consistency'"
        ).fetchone()[0]
    assert "'edit'::" not in definition and "'edit_and_accept'" in definition
    assert edit_guard == 0
    _migration(dsn, "0011_ai_chunk_edit_action.up.sql")
