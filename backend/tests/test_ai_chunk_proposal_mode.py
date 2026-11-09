"""R-M11: a batch's proposal_mode comes only from what its own candidates
recorded at generation (`proposal_model`), never from the current adapter.

mock: every candidate from a known mock. model: every candidate from a
trusted model id (none is trusted yet). unknown: empty, mixed or
unrecognised. The label is response-only and never persisted.
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.ai_chunks import service as service_module
from services.ai_chunks.adapter import ProposedChunk
from services.ai_chunks.service import AiChunkOrganizerService, proposal_mode_of
from services.ai_chunks.store import InMemoryAiChunkStore
from services.library.store import LearningLibraryStore
from services.schemas.ai_chunks import AiChunkBatch, CandidateDecisionRequest, ChunkSuggestionRequest
from services.study_bank.store import StudyBankStore
from test_durable_ai_chunk_candidates import (  # noqa: F401  (fixture)
    OWNER_A,
    _as_owner,
    _lesson,
    _service,
    cloud_candidates,
)

NOW = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)
USER = "local-learner"
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ai_chunk_legacy_v1.json"
client = TestClient(app_module.app)


class OtherAdapter:
    """Any non-mock adapter, e.g. after a configuration change."""
    model_id = "some-model"
    proposal_version = "1"

    def propose(self, *, language, segments, max_candidates):
        return [ProposedChunk(text="it depends on", evidence_segment_id=segments[0].segment_id)]


def _manual(text, key):
    return ChunkSuggestionRequest.model_validate({
        "user_id": USER, "source_language": "en", "idempotency_key": key,
        "source": {"source_type": "manual_text", "text": text},
    })


@pytest.fixture
def personal(tmp_path):
    store = InMemoryAiChunkStore()
    library = LearningLibraryStore(tmp_path / "l.json", media_root=tmp_path / "m", clock=lambda: NOW)
    study = StudyBankStore(tmp_path / "s.json", clock=lambda: NOW)

    def service(adapter=None):
        return AiChunkOrganizerService(library, study, adapter=adapter, store=store, clock=lambda: NOW)

    return service, store


def test_a_historical_mock_batch_stays_mock_after_the_adapter_changes(personal):
    make, _ = personal
    created = make().suggest(USER, _manual("As far as I know, it depends on it.", "rm11-history-01"))
    later = make(OtherAdapter())

    read = later.describe(later.get_batch(USER, created.batch_id))
    replay = later.describe(later.suggest(USER, _manual("As far as I know, it depends on it.", "rm11-history-01")))

    assert {c.proposal_model for c in created.candidates} == {"mock-chunk-organizer"}
    assert read.proposal_mode == replay.proposal_mode == "mock"
    assert replay.model_copy(update={"proposal_mode": None}) == created  # replay content unchanged


def test_an_empty_batch_is_unknown_on_create_read_and_replay(personal):
    make, _ = personal
    service = make()
    created = service.describe(service.suggest(USER, _manual("Nothing known here at all.", "rm11-empty-0001")))

    read = service.describe(service.get_batch(USER, created.batch_id))
    replay = service.describe(service.suggest(USER, _manual("Nothing known here at all.", "rm11-empty-0001")))

    assert created.candidates == []
    assert created.proposal_mode == read.proposal_mode == replay.proposal_mode == "unknown"


def test_an_unrecognised_model_is_unknown_not_model(personal):
    make, _ = personal
    service = make(OtherAdapter())

    batch = service.describe(service.suggest(USER, _manual("It depends on you.", "rm11-other-0001")))

    assert [c.proposal_model for c in batch.candidates] == ["some-model"]
    assert batch.proposal_mode == "unknown"


def test_only_a_trusted_model_id_is_labelled_model(personal, monkeypatch):
    make, _ = personal
    batch = make(OtherAdapter()).suggest(USER, _manual("It depends on you.", "rm11-trusted-01"))

    monkeypatch.setattr(service_module, "TRUSTED_MODEL_PROPOSAL_MODELS", frozenset({"some-model"}))

    assert proposal_mode_of(batch) == "model"


def test_a_mixed_batch_is_unknown(personal):
    make, _ = personal
    mock = make().suggest(USER, _manual("As far as I know, it works.", "rm11-mixed-0001"))
    other = make(OtherAdapter()).suggest(USER, _manual("It depends on you.", "rm11-mixed-0002"))
    mixed = mock.model_copy(update={"candidates": mock.candidates + other.candidates})

    assert proposal_mode_of(mixed) == "unknown"


def test_decisions_do_not_change_the_label_and_it_is_never_stored(personal):
    make, store = personal
    service = make()
    batch = service.suggest(USER, _manual("As far as I know, it works.", "rm11-decide-0001"))
    service.decide(USER, batch.batch_id, CandidateDecisionRequest.model_validate({
        "user_id": USER, "candidate_id": batch.candidates[0].candidate_id, "action": "reject",
        "expected_version": 1, "idempotency_key": "rm11-decide-key1",
    }))

    assert service.describe(service.get_batch(USER, batch.batch_id)).proposal_mode == "mock"
    assert store.get_batch(USER, batch.batch_id).proposal_mode is None


def test_the_label_ignores_any_incoming_value(personal):
    make, _ = personal
    batch = make().suggest(USER, _manual("As far as I know, it works.", "rm11-ignore-0001"))

    assert AiChunkOrganizerService.describe(batch.model_copy(update={"proposal_mode": "model"})).proposal_mode == "mock"
    empty = batch.model_copy(update={"candidates": [], "proposal_mode": "mock"})
    assert AiChunkOrganizerService.describe(empty).proposal_mode == "unknown"


def test_a_legacy_v1_batch_is_labelled_from_its_candidates():
    batch = AiChunkBatch.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8"))["batch"])

    assert batch.proposal_mode is None
    assert proposal_mode_of(batch) == "mock"


def test_cloud_labels_come_from_persisted_candidates_on_create_read_and_replay(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    item = library.create_for_owner(OWNER_A, _lesson())
    body = {"user_id": OWNER_A, "source_language": "en", "idempotency_key": "rm11-cloud-00001",
            "source": {"source_type": "library_passage", "library_item_id": item.id, "sentence_ids": [1]}}

    with _as_owner(OWNER_A, candidates, study, library):
        created = client.post("/api/ai/chunk-suggestions", json=body).json()
        read = client.get(f"/api/ai/chunk-suggestions/{created['batch_id']}", params={"user_id": OWNER_A}).json()
        replay = client.post("/api/ai/chunk-suggestions", json=body).json()
    later = _service(clock, study, library, candidates, OtherAdapter())
    after_switch = later.describe(later.get_batch(OWNER_A, created["batch_id"]))

    assert created["candidates"] and created["proposal_mode"] == "mock"
    assert read["proposal_mode"] == replay["proposal_mode"] == after_switch.proposal_mode == "mock"
    assert replay["batch_id"] == created["batch_id"]
    assert {k: v for k, v in replay.items() if k != "proposal_mode"} == \
        {k: v for k, v in created.items() if k != "proposal_mode"}
