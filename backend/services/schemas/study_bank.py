from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.schemas.lesson import SourceLanguage


StudyKind = Literal["word", "chunk"]
StudyQuality = Literal["recommended", "review", "rejected"]


class StudyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StudyProvenance(StudyModel):
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
    provenance: list[StudyProvenance] = Field(min_length=1, max_length=100)


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
    provenance: list[StudyProvenance] = Field(min_length=1, max_length=1_000)
    lemma: str | None = Field(default=None, max_length=500)
    part_of_speech: str | None = Field(default=None, max_length=128)
    example: str | None = Field(default=None, max_length=2_000)
    usage: str | None = Field(default=None, max_length=2_000)
    note: str | None = Field(default=None, max_length=2_000)

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
