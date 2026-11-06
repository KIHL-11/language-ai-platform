"""Phase A persistence projection for the candidate contract (Round 3, M4).

Executable model of CANDIDATE-COMPATIBILITY-CONTRACT.md §4. It is not wired
into production; production serialisation is unchanged.

- `*V2` models are the proposed new-server models: the current production
  models plus optional fields.
- The current production models stand in for the old server. They use
  `extra="forbid"`, so a stored record carrying any new key, even one whose
  value is None, would fail to load on an old server.
- `persisted_*` projections produce exactly the old schema's keys. In Phase A
  every persisted record (decision `response_json`, Personal JSON store) goes
  through them; API responses may carry the new fields.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from services.schemas.ai_chunks import (
    AiChunkBatch,
    AiChunkCandidate,
    CandidateDecisionResponse,
    CandidateEvidence,
)

PHASE_A_MAX_PERSISTED_CANDIDATES = 8  # the old server's AiChunkBatch limit


class Recommendation(BaseModel):
    status: Literal["recommended", "below_floor", "not_selected_tie", "not_selected_limit", "unscored", "gated",
                    "previously_rejected"]
    score: str | None = None  # decimal string, never a float
    dimensions: dict[str, int | None] = Field(default_factory=dict)
    weight_coverage: str | None = None
    tied_with: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class Variation(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    origin: Literal["ai_variation"] = "ai_variation"


class CandidateEvidenceV2(CandidateEvidence):
    transcript_revision: str | None = None
    sentence_fingerprint: str | None = None


class AiChunkCandidateV2(AiChunkCandidate):
    evidence: CandidateEvidenceV2
    origin: Literal["source_extracted"] = "source_extracted"
    verification: Literal["grounded", "manual_text", "revalidated", "invalid"] | None = None
    invalid_reason: str | None = None
    recommendation: Recommendation | None = None
    variations: list[Variation] = Field(default_factory=list, max_length=2)
    additional_sources: list[CandidateEvidenceV2] = Field(default_factory=list, max_length=5)


class CandidateDecisionResponseV2(CandidateDecisionResponse):
    candidate: AiChunkCandidateV2


class AiChunkBatchV2(AiChunkBatch):
    candidates: list[AiChunkCandidateV2] = Field(max_length=12)
    curation_job_id: str | None = None
    batch_revision: int | None = None
    coverage: dict | None = None


def _pick(data: dict, model: type[BaseModel]) -> dict:
    return {key: data[key] for key in model.model_fields if key in data}


def _persisted_candidate(data: dict) -> dict:
    candidate = _pick(data, AiChunkCandidate)
    candidate["evidence"] = _pick(data["evidence"], CandidateEvidence)
    return candidate


def persisted_decision(response: CandidateDecisionResponseV2 | CandidateDecisionResponse) -> dict:
    """The Phase A `response_json` for a decision: old-schema keys only."""
    data = response.model_dump(mode="json")
    record = _pick(data, CandidateDecisionResponse)
    record["candidate"] = _persisted_candidate(data["candidate"])
    CandidateDecisionResponse.model_validate(record)  # must load on the old server
    return record


def persisted_batch(batch: AiChunkBatchV2 | AiChunkBatch) -> dict:
    """A Phase A persisted batch (e.g. the Personal JSON store)."""
    if len(batch.candidates) > PHASE_A_MAX_PERSISTED_CANDIDATES:
        raise ValueError("Phase A cannot persist more candidates than the old server can load")
    data = batch.model_dump(mode="json")
    record = _pick(data, AiChunkBatch)
    record.pop("proposal_mode", None)  # computed per response, never persisted (Step 1)
    record["candidates"] = [_persisted_candidate(value) for value in data["candidates"]]
    AiChunkBatch.model_validate(record)
    return record
