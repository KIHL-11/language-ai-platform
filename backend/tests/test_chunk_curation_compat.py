"""Round 3, M4: executable rollback-compatibility checks for the Phase A
persistence projection (chunk_curation_lab/compat.py).

"Old server" = the current production pydantic models (extra="forbid").
"New server" = the proposed *V2 models. Decision responses are real ones from
the Personal organizer on temporary synthetic stores. Production
serialisation is not changed by this round.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from chunk_curation_lab.compat import (
    AiChunkBatchV2,
    AiChunkCandidateV2,
    CandidateDecisionResponseV2,
    Recommendation,
    Variation,
    persisted_batch,
    persisted_decision,
)
from services.schemas.ai_chunks import AiChunkBatch, AiChunkCandidate, CandidateDecisionResponse, CandidateEvidence
from services.schemas.study_bank import MediaStudyProvenance, StudyItemInput, StudyItemPatch
from test_ai_chunk_organizer import USER_A, decision, manual_request, organizer  # noqa: F401  (fixture)


def _keys(model, data):
    return set(data) - set(model.model_fields)


def _upgrade(response: CandidateDecisionResponse, **candidate_fields) -> CandidateDecisionResponseV2:
    """What a new server would hold in memory for the same decision."""
    data = response.model_dump(mode="json")
    data["candidate"]["evidence"]["transcript_revision"] = None
    data["candidate"].update(candidate_fields)
    return CandidateDecisionResponseV2.model_validate(data)


@pytest.fixture
def decisions(organizer):
    """Real Accept, Edit-and-accept and Reject responses, and a duplicate
    accept onto an archived card with a note, a manual meaning and SRS."""
    service, study, _ = organizer
    provenance = MediaStudyProvenance(
        library_item_id="existing-library", lesson_id="existing-lesson", source_url="https://example.invalid",
        media_title="Existing", sentence_id=1, sentence_text="As far as I know.", start=0, end=2,
    )
    [existing], _ = study.save_items([StudyItemInput(
        user_id=USER_A, kind="chunk", source_language="en", target_language="zh-CN",
        text="as far as I know", meaning="manual meaning", provenance=[provenance],
    )])
    study.rate_item(USER_A, existing.id, "good", 0)
    archived = study.update_item(USER_A, existing.id, StudyItemPatch(active=False, note="learner note"))
    batch = service.suggest(USER_A, manual_request())
    first, second = batch.candidates
    accepted = service.decide(USER_A, batch.batch_id, decision(first, key="compat-accept-0001"))
    rejected = service.decide(USER_A, batch.batch_id, decision(second, action="reject", key="compat-reject-001"))
    batch2 = service.suggest(USER_A, manual_request(text="It depends on the weather.", key="request-key-0002"))
    edited = service.decide(USER_A, batch2.batch_id, decision(
        batch2.candidates[0], action="edit_and_accept", key="compat-edit-00001",
        edit={"text": "it depends on", "suggested_meaning_zh": "看情况", "usage_note": "", "example": ""}))
    return {"accept": accepted, "reject": rejected, "edit_and_accept": edited}, archived, study, service.describe(batch)


def test_the_hazard_is_real_a_plain_dump_of_new_fields_breaks_the_old_server(decisions):
    responses, *_ = decisions
    upgraded = _upgrade(responses["accept"])  # every new field at its default / None

    plain = upgraded.model_dump(mode="json")

    assert _keys(AiChunkCandidate, plain["candidate"]) >= {"origin", "recommendation", "verification"}
    with pytest.raises(ValidationError):
        CandidateDecisionResponse.model_validate(plain)


@pytest.mark.parametrize("action", ["accept", "edit_and_accept", "reject"])
def test_new_server_phase_a_records_load_on_the_old_server(decisions, action):
    responses, *_ = decisions
    upgraded = _upgrade(
        responses[action], verification="grounded",
        recommendation=Recommendation(status="recommended", score="2.71").model_dump(),
        variations=[Variation(text="That depends on you.").model_dump()],
    )

    record = persisted_decision(upgraded)
    old = CandidateDecisionResponse.model_validate(json.loads(json.dumps(record)))

    assert not _keys(CandidateDecisionResponse, record)
    assert not _keys(AiChunkCandidate, record["candidate"])
    assert not _keys(CandidateEvidence, record["candidate"]["evidence"])
    assert old == responses[action]  # same decision, same study item, same flags


@pytest.mark.parametrize("action", ["accept", "edit_and_accept", "reject"])
def test_old_server_records_load_on_the_new_server_with_truthful_defaults(decisions, action):
    responses, *_ = decisions
    stored = json.loads(json.dumps(responses[action].model_dump(mode="json")))

    new = CandidateDecisionResponseV2.model_validate(stored)

    assert new.candidate.origin == "source_extracted"
    assert new.candidate.recommendation is None  # legacy: unscored, never "recommended"
    assert (new.candidate.variations, new.candidate.additional_sources) == ([], [])
    assert new.candidate.evidence.transcript_revision is None


@pytest.mark.parametrize("action", ["accept", "edit_and_accept", "reject"])
def test_replay_round_trips_without_rewriting_the_decision(decisions, action):
    responses, *_ = decisions
    original = responses[action].model_dump(mode="json")

    # old record -> new server reads -> new server persists (Phase A) -> old server reads
    through_new = persisted_decision(CandidateDecisionResponseV2.model_validate(original))
    replayed = CandidateDecisionResponse.model_validate(through_new)

    assert through_new == original  # byte-for-byte the same record
    assert replayed.candidate.state == responses[action].candidate.state
    assert replayed.candidate.version == responses[action].candidate.version
    assert replayed.candidate.accepted_study_item_id == responses[action].candidate.accepted_study_item_id


def test_edit_only_responses_round_trip_too(decisions):
    responses, *_ = decisions
    # An `edit` (Cloud) keeps the candidate proposed and has no study item.
    base = responses["reject"].model_dump(mode="json")
    base["candidate"].update(state="proposed", text="it depends on", normalized_identity="it depends on")
    edit = CandidateDecisionResponse.model_validate({**base, "study_item": None, "created": False})

    assert persisted_decision(CandidateDecisionResponseV2.model_validate(edit.model_dump(mode="json"))) == \
        edit.model_dump(mode="json")


def test_projection_preserves_archive_note_meaning_and_srs_exactly(decisions):
    responses, archived, study, _ = decisions
    accepted = responses["accept"]

    record = persisted_decision(_upgrade(accepted, verification="revalidated"))

    assert accepted.created is False and accepted.study_item.id == archived.id
    assert record["study_item"] == accepted.study_item.model_dump(mode="json")
    item = record["study_item"]
    assert (item["active"], item["meaning"], item["note"]) == (False, "manual meaning", "learner note")
    assert item["review"] == archived.review.model_dump(mode="json")
    assert study.get_item(USER_A, archived.id).review == archived.review  # the store was not changed


def test_phase_a_batches_drop_new_and_computed_fields_and_refuse_oversize(decisions):
    *_, batch = decisions
    assert batch.proposal_mode == "mock"
    data = batch.model_dump(mode="json")
    for candidate in data["candidates"]:
        candidate["evidence"]["sentence_fingerprint"] = None
        candidate["recommendation"] = None
    upgraded = AiChunkBatchV2.model_validate({**data, "batch_revision": 1, "curation_job_id": None})

    record = persisted_batch(upgraded)

    assert "proposal_mode" not in record and "batch_revision" not in record
    assert all(not _keys(AiChunkCandidate, c) for c in record["candidates"])
    assert AiChunkBatch.model_validate(record).batch_id == batch.batch_id
    twelve = upgraded.model_copy(update={"candidates": upgraded.candidates * 6})
    assert len(twelve.candidates) > 8
    with pytest.raises(ValueError, match="more candidates than the old server"):
        persisted_batch(twelve)


def test_the_v2_candidate_can_never_claim_a_variation_origin():
    data = {"text": "x", "origin": "ai_variation"}
    with pytest.raises(ValidationError):
        AiChunkCandidateV2.model_validate({**data})
    with pytest.raises(ValidationError):
        Variation.model_validate({"text": "x", "origin": "source_extracted"})
