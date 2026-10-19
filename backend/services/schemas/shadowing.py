from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ShadowingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimedWord(ShadowingModel):
    word: str = Field(min_length=1, max_length=500)
    start: float = Field(ge=0)
    end: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end < self.start:
            raise ValueError("Timed word end must not precede its start")
        return self


class AlignedWord(ShadowingModel):
    status: Literal["matched", "missed", "extra", "substituted"]
    target: str | None = None
    recognized: str | None = None


class ShadowingMetrics(ShadowingModel):
    recognized_text: str = Field(max_length=20_000)
    target_word_count: int = Field(ge=0)
    recognized_word_count: int = Field(ge=0)
    matched_count: int = Field(ge=0)
    missed_count: int = Field(ge=0)
    extra_count: int = Field(ge=0)
    substituted_count: int = Field(ge=0)
    transcript_match_percent: float = Field(ge=0, le=100)
    reference_speech_duration_ms: int | None = Field(default=None, ge=0)
    attempt_speech_duration_ms: int | None = Field(default=None, ge=0)
    duration_delta_ms: int | None = None
    duration_delta_ratio: float | None = Field(default=None, ge=0)
    start_delay_ms: int | None = None
    word_timing_mae_ms: int | None = Field(default=None, ge=0)
    pause_mae_ms: int | None = Field(default=None, ge=0)
    aligned_timed_word_count: int = Field(ge=0)
    timing_reference_available: bool
    timing_unavailable_reason: str | None = Field(default=None, max_length=200)
    sync_target_met: bool
    aligned_words: list[AlignedWord] = Field(default_factory=list, max_length=1_000)


class SentenceShadowingProgress(ShadowingModel):
    attempt_count: int = Field(default=0, ge=0)
    target_hit_count: int = Field(default=0, ge=0)
    consecutive_target_hits: int = Field(default=0, ge=0)
    last_attempt_at: datetime | None = None
    last_metrics: ShadowingMetrics | None = None
    best_transcript_match_percent: float | None = Field(default=None, ge=0, le=100)
    best_duration_delta_ratio: float | None = Field(default=None, ge=0)
    best_abs_start_delay_ms: int | None = Field(default=None, ge=0)
    best_word_timing_mae_ms: int | None = Field(default=None, ge=0)
    best_pause_mae_ms: int | None = Field(default=None, ge=0)

    @field_validator("last_attempt_at", mode="after")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Shadowing timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_counts(self):
        if self.target_hit_count > self.attempt_count:
            raise ValueError("Shadowing target hits cannot exceed attempts")
        if self.consecutive_target_hits > self.target_hit_count:
            raise ValueError("Consecutive target hits cannot exceed total hits")
        if self.attempt_count == 0:
            if self.last_attempt_at is not None or self.last_metrics is not None:
                raise ValueError("Unattempted sentence cannot have last metrics")
        elif self.last_attempt_at is None or self.last_metrics is None:
            raise ValueError("Attempted sentence requires last metrics and timestamp")
        return self


class ShadowingProgress(ShadowingModel):
    last_sentence_id: int | None = None
    last_practiced_at: datetime | None = None
    sentences: dict[str, SentenceShadowingProgress] = Field(default_factory=dict)

    @field_validator("last_practiced_at", mode="after")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Shadowing timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_latest_sentence(self):
        if not self.sentences:
            if self.last_sentence_id is not None or self.last_practiced_at is not None:
                raise ValueError("Unattempted Shadowing progress cannot have last activity")
        elif (
            self.last_sentence_id is None
            or str(self.last_sentence_id) not in self.sentences
            or self.last_practiced_at is None
        ):
            raise ValueError("Shadowing progress requires a valid latest sentence")
        return self


class ShadowingAttemptResponse(ShadowingModel):
    sentence_id: int
    progress: SentenceShadowingProgress
    metrics: ShadowingMetrics
    sync_target_met: bool
    stable_sync_achieved: bool
