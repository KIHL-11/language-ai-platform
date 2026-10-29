from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.schemas.lesson import SourceLanguage
from services.schemas.study_bank import StudyItem


CandidateState = Literal[
    "proposed", "accepted", "edited_and_accepted", "rejected", "expired"
]
DecisionAction = Literal["accept", "edit_and_accept", "reject"]


class AiChunkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ManualTextSource(AiChunkModel):
    source_type: Literal["manual_text"] = "manual_text"
    text: str = Field(min_length=1, max_length=2_000)
    label: str = Field(default="Manual learning text", min_length=1, max_length=200)


class LibraryPassageSource(AiChunkModel):
    source_type: Literal["library_passage"] = "library_passage"
    library_item_id: str = Field(min_length=1, max_length=128)
    sentence_ids: list[int] = Field(min_length=1, max_length=12)

    @field_validator("sentence_ids")
    @classmethod
    def sentence_ids_are_unique(cls, values: list[int]):
        if any(value <= 0 for value in values) or len(set(values)) != len(values):
            raise ValueError("sentence_ids must be unique positive integers")
        return values


SuggestionSource = Annotated[
    ManualTextSource | LibraryPassageSource, Field(discriminator="source_type")
]


class ChunkSuggestionRequest(AiChunkModel):
    user_id: str = Field(min_length=1, max_length=128)
    source_language: SourceLanguage
    source: SuggestionSource
    max_candidates: int = Field(default=5, ge=1, le=8)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


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
    user_id: str = Field(min_length=1, max_length=128)
    source_language: SourceLanguage
    created_at: datetime
    candidates: list[AiChunkCandidate] = Field(max_length=8)

    @field_validator("created_at", mode="after")
    @classmethod
    def timestamp_is_utc(cls, value: datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candidate batch timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)


class CandidateEdit(AiChunkModel):
    text: str = Field(min_length=1, max_length=500)
    suggested_meaning_zh: str = Field(default="", max_length=1_000)
    usage_note: str = Field(default="", max_length=2_000)
    example: str = Field(default="", max_length=2_000)


class CandidateDecisionRequest(AiChunkModel):
    user_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=64)
    action: DecisionAction
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    edit: CandidateEdit | None = None

    @model_validator(mode="after")
    def edit_matches_action(self):
        if self.action == "edit_and_accept" and self.edit is None:
            raise ValueError("edit is required for edit_and_accept")
        if self.action != "edit_and_accept" and self.edit is not None:
            raise ValueError("edit is only valid for edit_and_accept")
        return self


class CandidateDecisionResponse(AiChunkModel):
    candidate: AiChunkCandidate
    study_item: StudyItem | None = None
    created: bool = False
    warnings: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=5
    )
