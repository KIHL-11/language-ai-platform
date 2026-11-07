"""Frozen V1 is an independent old reader, not today's production classes."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError, create_model
import pytest

from chunk_curation_lab import compat, legacy_v1
from chunk_curation_lab.compat import (
    AiChunkBatchV2, CandidateDecisionResponseV2, persisted_batch, persisted_decision,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ai_chunk_legacy_v1.json"


def _fixture():
    return json.loads(FIXTURE.read_text())


@pytest.mark.parametrize("action", ["accept", "edit_and_accept", "reject", "edit"])
def test_frozen_historical_record_round_trips_through_v2_without_upgrading(action):
    record = _fixture()["decisions"][action]
    old = legacy_v1.CandidateDecisionResponse.model_validate(record)
    new = CandidateDecisionResponseV2.model_validate(record)

    assert persisted_decision(new) == record
    assert old.model_dump(mode="json") == record
    assert new.candidate.recommendation is None
    assert new.candidate.evidence.transcript_revision is None


def test_historical_batch_keeps_v1_proposal_version_and_exact_persisted_shape():
    record = _fixture()["batch"]
    assert legacy_v1.AiChunkBatch.model_validate(record).candidates[0].proposal_version == "1"
    assert persisted_batch(AiChunkBatchV2.model_validate(record)) == record
    with pytest.raises(ValidationError):
        legacy_v1.AiChunkBatch.model_validate({**record, "source_type": "library_curation"})
    with pytest.raises(ValidationError):
        legacy_v1.AiChunkBatch.model_validate({**record, "candidates": record["candidates"] * 9})


def test_projection_does_not_follow_future_changes_to_live_models(monkeypatch):
    # Simulate the future production models growing optional fields. The old
    # reader and projection must not grow with them.
    for name in ("AiChunkCandidate", "CandidateEvidence", "CandidateDecisionResponse", "AiChunkBatch"):
        current = getattr(compat, name)
        future = create_model(name + "Future", __base__=current, future_optional=(str | None, None))
        monkeypatch.setattr(compat, name, future)
    record = _fixture()["decisions"]["accept"]
    expanded = deepcopy(record)
    expanded["future_optional"] = None
    expanded["candidate"]["future_optional"] = None
    expanded["candidate"]["evidence"]["future_optional"] = None
    expanded["study_item"]["future_optional"] = None
    expanded["study_item"]["review"]["future_optional"] = None
    for source in expanded["study_item"]["provenance"]:
        source["future_optional"] = None

    # A model_dump-shaped input simulates all future nested additions without
    # importing or modifying any production class.
    response = SimpleNamespace(model_dump=lambda **_: deepcopy(expanded))
    assert persisted_decision(response) == record
    assert "future_optional" not in legacy_v1.AiChunkCandidate.model_fields

    batch = _fixture()["batch"]
    expanded_batch = deepcopy(batch)
    expanded_batch["future_optional"] = None
    expanded_batch["candidates"][0]["future_optional"] = None
    expanded_batch["candidates"][0]["evidence"]["future_optional"] = None
    stub = SimpleNamespace(candidates=[None], model_dump=lambda **_: deepcopy(expanded_batch))
    assert persisted_batch(stub) == batch


@pytest.mark.parametrize("target", ["candidate", "evidence", "study_item", "review", "provenance"])
def test_frozen_old_reader_rejects_new_keys_at_every_persisted_depth(target):
    record = _fixture()["decisions"]["accept"]
    locations = {
        "candidate": record["candidate"],
        "evidence": record["candidate"]["evidence"],
        "study_item": record["study_item"],
        "review": record["study_item"]["review"],
        "provenance": record["study_item"]["provenance"][0],
    }
    locations[target]["new_key"] = None
    with pytest.raises(ValidationError):
        legacy_v1.CandidateDecisionResponse.model_validate(record)


def test_new_provenance_types_fail_closed_in_phase_a():
    record = _fixture()["decisions"]["accept"]
    record["study_item"]["provenance"][0]["source_type"] = "future_source"
    response = SimpleNamespace(model_dump=lambda **_: deepcopy(record))
    with pytest.raises(ValueError, match="non-Legacy provenance"):
        persisted_decision(response)


def test_frozen_contract_has_no_production_schema_imports():
    import ast
    tree = ast.parse(Path(legacy_v1.__file__).read_text())
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(name.startswith("services") for name in imports)
    assert legacy_v1.LEGACY_V1_BASELINE == "19a8830"
