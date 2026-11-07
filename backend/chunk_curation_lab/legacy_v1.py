"""Frozen Legacy V1 persisted contract from 19a8830 (F2 experiment only).

Intentional static copies of baseline validators, including StudyItem,
ReviewState and all provenance variants. No imports of production models.
Do not refresh this module when V2 evolves: introduce another version instead.
Not imported by any production service; no data migration or I/O.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LEGACY_V1_BASELINE = "19a8830"
SourceLanguage = Literal["en", "de"]
StudyKind = Literal["word", "chunk"]
ReviewRating = Literal["again", "hard", "good", "easy"]
MIN_EASE = 1.3
DEFAULT_EASE = 2.5
MAX_EASE = 3.0
CandidateState = Literal["proposed", "accepted", "edited_and_accepted", "rejected", "expired"]
BatchState = Literal["ready", "expired"]

class StudyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReviewState(StudyModel):
    due_at: datetime
    interval_seconds: int = Field(ge=0)
    ease_factor: float = Field(ge=MIN_EASE, le=MAX_EASE)
    repetition_count: int = Field(ge=0)
    lapse_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    again_count: int = Field(ge=0)
    hard_count: int = Field(ge=0)
    good_count: int = Field(ge=0)
    easy_count: int = Field(ge=0)
    last_reviewed_at: datetime | None = None
    last_rating: ReviewRating | None = None

    @field_validator("due_at", "last_reviewed_at", mode="after")
    @classmethod
    def timestamp_is_utc(cls, value: datetime | None):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Review timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_history(self):
        rating_total = (
            self.again_count + self.hard_count + self.good_count + self.easy_count
        )
        if rating_total != self.review_count:
            raise ValueError("Review rating counters must equal review_count")
        if self.review_count == 0:
            if self.last_reviewed_at is not None or self.last_rating is not None:
                raise ValueError("Unreviewed items cannot have a last review")
        elif self.last_reviewed_at is None or self.last_rating is None:
            raise ValueError("Reviewed items require a last review and rating")
        return self

    @classmethod
    def new(cls, created_at: datetime) -> "ReviewState":
        return cls(
            due_at=created_at,
            interval_seconds=0,
            ease_factor=DEFAULT_EASE,
            repetition_count=0,
            lapse_count=0,
            review_count=0,
            again_count=0,
            hard_count=0,
            good_count=0,
            easy_count=0,
        )


class MediaStudyProvenance(StudyModel):
    source_type: Literal["media"] = "media"
    library_item_id: str = Field(min_length=1, max_length=128)
    lesson_id: str = Field(min_length=1, max_length=128)
    source_url: str = Field(min_length=1, max_length=2_048)
    media_title: str = Field(min_length=1, max_length=1_000)
    sentence_id: int = Field(gt=0)
    sentence_text: str = Field(min_length=1, max_length=20_000)
    sentence_translation: str = Field(default="", max_length=20_000)
    start: float = Field(ge=0)
    end: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end < self.start:
            raise ValueError("provenance end must be greater than or equal to start")
        return self


class SpeakingStudyProvenance(StudyModel):
    source_type: Literal["speaking"] = "speaking"
    speaking_session_id: str = Field(min_length=1, max_length=128)
    suggestion_index: int = Field(ge=0, le=7)
    originating_turn: int = Field(ge=1)
    provider: Literal["gemini-live", "openai-realtime"]
    mode: Literal["review_weak", "media", "custom"]
    media_title_snapshot: str | None = Field(default=None, max_length=1_000)
    evidence_text: str | None = Field(default=None, max_length=2_000)


class AiTextStudyProvenance(StudyModel):
    """Immutable evidence snapshot for an explicitly submitted text passage."""

    source_type: Literal["ai_text"] = "ai_text"
    source_label: str = Field(default="Manual learning text", min_length=1, max_length=200)
    source_text: str = Field(min_length=1, max_length=6_000)
    evidence_start: int = Field(ge=0)
    evidence_end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_evidence_range(self):
        if self.evidence_end <= self.evidence_start:
            raise ValueError("AI text evidence end must be after start")
        if self.evidence_end > len(self.source_text):
            raise ValueError("AI text evidence range exceeds source text")
        return self

StudyProvenanceRecord = Annotated[
    MediaStudyProvenance | SpeakingStudyProvenance | AiTextStudyProvenance,
    Field(discriminator="source_type"),
]

class StudyItem(StudyModel):
    id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=128)
    kind: StudyKind
    source_language: SourceLanguage
    target_language: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=500)
    normalized_text: str = Field(min_length=1, max_length=500)
    meaning: str = Field(default="", max_length=1_000)
    active: bool = True
    created_at: datetime
    updated_at: datetime
    provenance: list[StudyProvenanceRecord] = Field(min_length=1, max_length=1_000)
    lemma: str | None = Field(default=None, max_length=500)
    part_of_speech: str | None = Field(default=None, max_length=128)
    example: str | None = Field(default=None, max_length=2_000)
    usage: str | None = Field(default=None, max_length=2_000)
    note: str | None = Field(default=None, max_length=2_000)
    review: ReviewState
    revision: int = Field(default=1, ge=1)

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def timestamp_is_utc(cls, value: datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Study Bank timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_timestamps(self):
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class AiChunkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CandidateEvidence(AiChunkModel):
    source_type: Literal["manual_text", "library_sentence"]
    quote: str = Field(min_length=1, max_length=2_000)
    source_label: str = Field(min_length=1, max_length=1_000)
    library_item_id: str | None = Field(default=None, max_length=128)
    lesson_id: str | None = Field(default=None, max_length=128)
    sentence_id: int | None = Field(default=None, gt=0)
    source_url: str | None = Field(default=None, max_length=2_048)
    sentence_translation: str = Field(default="", max_length=2_000)
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)
    evidence_start: int = Field(ge=0)
    evidence_end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_source(self):
        if self.evidence_end <= self.evidence_start or self.evidence_end > len(self.quote):
            raise ValueError("candidate evidence range is invalid")
        media_fields = (
            self.library_item_id,
            self.lesson_id,
            self.sentence_id,
            self.source_url,
            self.start,
            self.end,
        )
        if self.source_type == "library_sentence" and any(value is None for value in media_fields):
            raise ValueError("library sentence evidence is incomplete")
        if self.source_type == "manual_text" and any(value is not None for value in media_fields):
            raise ValueError("manual text evidence cannot claim media metadata")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("candidate media evidence range is invalid")
        return self


class AiChunkCandidate(AiChunkModel):
    candidate_id: str = Field(min_length=1, max_length=64)
    state: CandidateState = "proposed"
    version: int = Field(default=1, ge=1)
    source_language: SourceLanguage
    text: str = Field(min_length=1, max_length=500)
    normalized_identity: str = Field(min_length=1, max_length=500)
    suggested_meaning_zh: str = Field(default="", max_length=1_000)
    usage_note: str = Field(default="", max_length=2_000)
    example: str = Field(default="", max_length=2_000)
    explanation_of_value: str = Field(default="", max_length=1_000)
    quality_flags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=20
    )
    evidence: CandidateEvidence
    proposal_model: str = Field(min_length=1, max_length=100)
    proposal_version: str = Field(min_length=1, max_length=50)
    existing_study_item_id: str | None = Field(default=None, max_length=64)
    existing_study_item_active: bool | None = None
    accepted_study_item_id: str | None = Field(default=None, max_length=64)


class AiChunkBatch(AiChunkModel):
    batch_id: str = Field(min_length=1, max_length=64)
    # How the candidates were proposed: the offline "mock" phrase list or a
    # "model". Set on every response; None only on stored copies.
    proposal_mode: Literal["mock", "model"] | None = None
    user_id: str = Field(min_length=1, max_length=128)
    source_language: SourceLanguage
    source_type: Literal["manual_text", "library_passage"] = "manual_text"
    source_label: str = Field(default="Candidate source", min_length=1, max_length=1_000)
    created_at: datetime
    expires_at: datetime | None = None
    status: BatchState = "ready"
    candidates: list[AiChunkCandidate] = Field(max_length=8)

    @field_validator("created_at", mode="after")
    @classmethod
    def timestamp_is_utc(cls, value: datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candidate batch timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("expires_at", mode="after")
    @classmethod
    def expiry_is_utc(cls, value: datetime | None):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candidate batch expiry must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def expiry_follows_creation(self):
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("candidate batch expiry must follow creation")
        return self


class CandidateDecisionResponse(AiChunkModel):
    candidate: AiChunkCandidate
    study_item: StudyItem | None = None
    created: bool = False
    warnings: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=5
    )
