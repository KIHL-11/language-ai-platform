from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.schemas.lesson import LessonLevel, SourceLanguage


SpeakingMode = Literal["review_weak", "media", "custom"]
SpeakingProvider = Literal["gemini-live", "openai-realtime"]
SpeakingStatus = Literal["active", "completed", "interrupted", "failed"]
SpeakingEndReason = Literal["user", "time_limit", "provider", "error", "page_exit"]


class SpeakingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SpeakingTarget(SpeakingModel):
    study_item_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=500)
    meaning: str = Field(default="", max_length=1_000)


class SpeakingWord(SpeakingTarget):
    pass


class SpeakingMediaSentence(SpeakingModel):
    sentence_id: int
    text: str = Field(max_length=20_000)
    translation: str = Field(default="", max_length=20_000)
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class SpeakingContext(SpeakingModel):
    language: SourceLanguage
    level: LessonLevel
    mode: SpeakingMode
    session_goal: str = Field(max_length=1_000)
    target_chunks: list[SpeakingTarget] = Field(max_length=8)
    support_words: list[SpeakingWord] = Field(max_length=5)
    library_item_id: str | None = Field(default=None, max_length=128)
    media_title: str | None = Field(default=None, max_length=1_000)
    media_sentences: list[SpeakingMediaSentence] = Field(default_factory=list, max_length=12)
    system_prompt: str = Field(max_length=30_000)


class SpeakingContextRequest(SpeakingModel):
    user_id: str = Field(min_length=1, max_length=128)
    language: SourceLanguage
    level: LessonLevel
    mode: SpeakingMode
    library_item_id: str | None = Field(default=None, max_length=128)
    goal: str | None = Field(default=None, max_length=1_000)
    target_limit: int = Field(default=5, ge=0, le=8)
    target_ids: list[str] | None = Field(default=None, max_length=8)


class SpeakingTurn(SpeakingModel):
    sequence: int = Field(ge=1)
    role: Literal["learner", "agent"]
    text: str = Field(min_length=1, max_length=5_000)
    created_at: datetime
    provider_turn_id: str | None = Field(default=None, max_length=256)

    @field_validator("created_at", mode="after")
    @classmethod
    def utc_timestamp(cls, value: datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Speaking timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)


class SpeakingCorrection(SpeakingModel):
    learner_text: str = Field(min_length=1, max_length=2_000)
    corrected_text: str = Field(min_length=1, max_length=2_000)
    short_explanation: str = Field(min_length=1, max_length=1_000)
    category: Literal["grammar", "word_choice", "target_chunk", "clarity", "other"]


class SpeakingSuggestion(SpeakingModel):
    text: str = Field(min_length=1, max_length=500)
    meaning: str = Field(default="", max_length=1_000)
    kind: Literal["word", "chunk"]
    short_usage_note: str = Field(default="", max_length=1_000)
    originating_turn: int = Field(ge=1)


class TargetUsage(SpeakingTarget):
    use_count: int = Field(default=0, ge=0)
    first_used_turn: int | None = Field(default=None, ge=1)
    last_used_turn: int | None = Field(default=None, ge=1)
    evidence: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_usage(self):
        if self.use_count == 0 and (self.first_used_turn or self.last_used_turn or self.evidence):
            raise ValueError("Unused targets cannot contain evidence")
        if self.use_count > 0 and (self.first_used_turn is None or self.last_used_turn is None):
            raise ValueError("Used targets require turn evidence")
        return self


class SpeakingSession(SpeakingModel):
    id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    language: SourceLanguage
    level: LessonLevel
    mode: SpeakingMode
    provider: SpeakingProvider
    model: str = Field(min_length=1, max_length=256)
    requested_duration_seconds: Literal[180, 300, 600]
    library_item_id: str | None = Field(default=None, max_length=128)
    media_title_snapshot: str | None = Field(default=None, max_length=1_000)
    goal: str | None = Field(default=None, max_length=1_000)
    started_at: datetime
    ended_at: datetime | None = None
    status: SpeakingStatus = "active"
    event_count: int = Field(default=0, ge=0)
    target_chunks: list[SpeakingTarget] = Field(max_length=8)
    support_words: list[SpeakingWord] = Field(max_length=5)
    learner_turn_count: int = Field(default=0, ge=0)
    agent_turn_count: int = Field(default=0, ge=0)
    turns: list[SpeakingTurn] = Field(default_factory=list, max_length=40)
    corrections: list[SpeakingCorrection] = Field(default_factory=list, max_length=8)
    suggestions: list[SpeakingSuggestion] = Field(default_factory=list, max_length=8)
    target_usage: list[TargetUsage] = Field(max_length=8)
    end_reason: SpeakingEndReason | None = None

    @field_validator("started_at", "ended_at", mode="after")
    @classmethod
    def utc_timestamps(cls, value: datetime | None):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Speaking timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)


class SpeakingSessionCreate(SpeakingContextRequest):
    provider: SpeakingProvider
    duration_seconds: Literal[180, 300, 600] = 300


class SpeakingEventRequest(SpeakingModel):
    expected_event_count: int = Field(ge=0)
    type: Literal["turn", "correction", "suggestion"]
    role: Literal["learner", "agent"] | None = None
    text: str | None = Field(default=None, max_length=5_000)
    provider_turn_id: str | None = Field(default=None, max_length=256)
    correction: SpeakingCorrection | None = None
    suggestion: SpeakingSuggestion | None = None

    @model_validator(mode="after")
    def validate_payload(self):
        if self.type == "turn" and (self.role is None or not (self.text or "").strip()):
            raise ValueError("Final transcript turn requires role and text")
        if self.type == "correction" and self.correction is None:
            raise ValueError("Correction event requires correction")
        if self.type == "suggestion" and self.suggestion is None:
            raise ValueError("Suggestion event requires suggestion")
        return self


class SpeakingFinishRequest(SpeakingModel):
    expected_event_count: int = Field(ge=0)
    status: Literal["completed", "interrupted", "failed"]
    end_reason: SpeakingEndReason


class SpeakingSessionCreateResponse(SpeakingModel):
    session: SpeakingSession
    context: SpeakingContext


class SpeakingSessionList(SpeakingModel):
    sessions: list[SpeakingSession]
