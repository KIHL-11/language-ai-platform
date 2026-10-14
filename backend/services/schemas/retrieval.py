import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.schemas.lesson import Lesson, SourceLanguage


class RetrievalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def normalize_chunk_text(text: str, language: SourceLanguage) -> str:
    """Normalize only properties that are safe for deterministic deduplication."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = normalized.rstrip(" .!?。！？")
    if language == "en":
        normalized = normalized.casefold()
    return normalized


class StoredChunk(RetrievalModel):
    id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(default="legacy", min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=500)
    normalized_text: str = Field(min_length=1, max_length=500)
    meaning: str = Field(default="", max_length=1_000)
    source_language: SourceLanguage
    source_lesson_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_media_id: str | None = Field(default=None, min_length=1, max_length=256)
    source_sentence_id: int | None = Field(default=None, gt=0)
    source_stage: str | None = Field(default=None, min_length=1, max_length=64)
    created_at: datetime
    updated_at: datetime
    last_practiced_at: datetime | None = None
    practice_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    mastery_score: float = Field(default=0.0, ge=0.0, le=1.0)
    active: bool = True

    @field_validator(
        "created_at", "updated_at", "last_practiced_at", mode="after"
    )
    @classmethod
    def normalize_timestamp(cls, value: datetime | None):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="before")
    @classmethod
    def fill_legacy_fields(cls, value: Any):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        language = data.get("source_language")
        text = data.get("text")
        if not data.get("normalized_text") and isinstance(text, str) and language in {
            "en",
            "de",
        }:
            data["normalized_text"] = normalize_chunk_text(text, language)
        if data.get("updated_at") is None and data.get("created_at") is not None:
            data["updated_at"] = data["created_at"]
        return data

    @model_validator(mode="after")
    def validate_statistics(self):
        if self.success_count + self.failure_count > self.practice_count:
            raise ValueError(
                "success_count plus failure_count cannot exceed practice_count"
            )
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        if (
            self.last_practiced_at is not None
            and self.last_practiced_at < self.created_at
        ):
            raise ValueError(
                "last_practiced_at cannot be earlier than created_at"
            )
        return self


class RetrievedChunk(StoredChunk):
    score: float = Field(ge=-1.0, le=1.0)


class ChunkSearchRequest(RetrievalModel):
    query: str = Field(min_length=1)
    source_language: SourceLanguage
    top_k: int = Field(default=5, ge=1, le=50)
    user_id: str = Field(default="legacy", min_length=1, max_length=128)


class ChunkSearchResponse(RetrievalModel):
    results: list[RetrievedChunk]


class StoreLessonChunksResponse(RetrievalModel):
    added: int
    total: int


PracticeOutcome = Literal["success", "partial", "failure"]


class ChunkMemoryCreateRequest(RetrievalModel):
    user_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=500)
    meaning: str = Field(default="", max_length=1_000)
    language: SourceLanguage
    source_lesson_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_media_id: str | None = Field(default=None, min_length=1, max_length=256)
    source_stage: str | None = Field(default=None, min_length=1, max_length=64)


class ChunkMemoryUpsertResponse(RetrievalModel):
    chunk: StoredChunk
    created: bool


class ChunkMemoryListResponse(RetrievalModel):
    chunks: list[StoredChunk]


class ChunkPracticeRequest(RetrievalModel):
    user_id: str = Field(min_length=1, max_length=128)
    outcome: PracticeOutcome


class ChunkActiveRequest(RetrievalModel):
    user_id: str = Field(min_length=1, max_length=128)
    active: bool


class ActiveRecallResponse(RetrievalModel):
    chunks: list[StoredChunk]


class StoreLessonMemoryRequest(RetrievalModel):
    user_id: str = Field(min_length=1, max_length=128)
    lesson: Lesson
    source_media_id: str | None = Field(default=None, min_length=1, max_length=256)

