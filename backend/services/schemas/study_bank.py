from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.schemas.lesson import SourceLanguage


StudyKind = Literal["word", "chunk"]
StudyQuality = Literal["recommended", "review", "rejected"]
ReviewRating = Literal["again", "hard", "good", "easy"]

MIN_EASE = 1.3
DEFAULT_EASE = 2.5
MAX_EASE = 3.0


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


# Backward-compatible name used by media curation call sites and fixtures.
StudyProvenance = MediaStudyProvenance
StudyProvenanceRecord = Annotated[
    MediaStudyProvenance | SpeakingStudyProvenance | AiTextStudyProvenance,
    Field(discriminator="source_type"),
]


class StudyItemInput(StudyModel):
    user_id: str = Field(min_length=1, max_length=128)
    kind: StudyKind
    source_language: SourceLanguage
    target_language: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=500)
    meaning: str = Field(default="", max_length=1_000)
    lemma: str | None = Field(default=None, max_length=500)
    part_of_speech: str | None = Field(default=None, max_length=128)
    example: str | None = Field(default=None, max_length=2_000)
    usage: str | None = Field(default=None, max_length=2_000)
    note: str | None = Field(default=None, max_length=2_000)
    provenance: list[StudyProvenanceRecord] = Field(min_length=1, max_length=100)


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


class StudyCandidate(StudyModel):
    candidate_id: str
    kind: StudyKind
    source_language: SourceLanguage
    target_language: str
    text: str
    normalized_text: str
    meaning: str = ""
    lemma: str | None = None
    part_of_speech: str | None = None
    example: str | None = None
    usage: str | None = None
    quality: StudyQuality
    quality_reasons: list[str]
    provenance: StudyProvenance
    already_saved: bool
    study_item_id: str | None = None


class StudyCandidateResponse(StudyModel):
    candidates: list[StudyCandidate]


class StudyBulkRequest(StudyModel):
    items: list[StudyItemInput] = Field(min_length=1, max_length=100)


class StudyBulkResult(StudyModel):
    items: list[StudyItem]
    created: int


class ContextualWordSaveRequest(StudyModel):
    user_id: str = Field(min_length=1, max_length=128)
    sentence_id: int = Field(gt=0)
    word: str = Field(min_length=1, max_length=100)

    @field_validator("word")
    @classmethod
    def lexical_word_only(cls, value: str):
        if not any(character.isalpha() for character in value):
            raise ValueError("word must contain lexical characters")
        return value


class ContextualWordSaveResult(StudyModel):
    item: StudyItem
    created: bool


class ContextualWordStatusResult(StudyModel):
    status: Literal["unsaved", "saved", "archived"]
    item: StudyItem | None = None


class StudyListResponse(StudyModel):
    items: list[StudyItem]


class StudyItemPatch(StudyModel):
    active: bool | None = None
    meaning: str | None = Field(default=None, max_length=1_000)
    note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def require_update(self):
        if self.active is None and self.meaning is None and self.note is None:
            raise ValueError("At least one editable Study Bank field is required")
        return self
