from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from services.schemas.lesson import LessonLevel, SourceLanguage
from services.schemas.speaking import SpeakingCorrection, SpeakingTarget, TargetUsage


class TopicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TopicCheckpoint(TopicModel):
    id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    label: str = Field(min_length=1, max_length=240)
    evidence_terms: list[str] = Field(min_length=1, max_length=8)


class SpeakingTopic(TopicModel):
    topic_id: str = Field(pattern=r"^(en|de)-[a-z0-9-]{1,80}$")
    version: int = Field(ge=1)
    language: SourceLanguage
    level: LessonLevel
    label: str = Field(min_length=1, max_length=160)
    scenario: str = Field(min_length=1, max_length=1_000)
    goal: str = Field(min_length=1, max_length=500)
    learner_role: str = Field(min_length=1, max_length=300)
    partner_role: str = Field(min_length=1, max_length=300)
    success_criteria: list[TopicCheckpoint] = Field(min_length=2, max_length=5)
    guided_prompts: list[str] = Field(min_length=2, max_length=6)
    support_expressions: list[str] = Field(default_factory=list, max_length=8)
    keywords: list[str] = Field(default_factory=list, max_length=16, exclude=True)
    state: Literal["published"] = "published"


class TopicCatalogResponse(TopicModel):
    catalog_version: int
    topics: list[SpeakingTopic] = Field(max_length=50)


class TopicPreviewRequest(TopicModel):
    user_id: str = Field(min_length=1, max_length=128)
    target_limit: int = Field(default=5, ge=3, le=5)


class TopicPreviewResponse(TopicModel):
    topic: SpeakingTopic
    target_chunks: list[SpeakingTarget] = Field(max_length=5)
    lifecycle: list[str] = Field(max_length=8)
    evidence_notice: str = Field(max_length=500)


class TopicAttemptStartRequest(TopicModel):
    user_id: str = Field(min_length=1, max_length=128)
    target_ids: list[str] = Field(default_factory=list, max_length=5)
    topic_version: int = Field(default=1, ge=1)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class TopicAttemptTurnRequest(TopicModel):
    user_id: str = Field(min_length=1, max_length=128)
    expected_event_count: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=2_000)
    topic_version: int = Field(default=1, ge=1)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class TopicAttemptFinishRequest(TopicModel):
    user_id: str = Field(min_length=1, max_length=128)
    expected_event_count: int = Field(ge=0)
    interrupted: bool = False
    topic_version: int = Field(default=1, ge=1)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class TopicAttemptState(TopicModel):
    session_id: str
    topic_id: str
    topic_version: int
    phase: Literal["live", "ended", "feedback", "saved"]
    event_count: int = Field(ge=0)
    learner_turn_count: int = Field(ge=0)
    target_usage: list[TargetUsage] = Field(max_length=5)
    prompt: str | None = Field(default=None, max_length=1_000)


class TopicAttemptTurnResponse(TopicModel):
    state: TopicAttemptState
    agent_reply: str = Field(min_length=1, max_length=1_000)


class TopicCheckpointResult(TopicModel):
    checkpoint_id: str
    label: str
    observed: bool
    evidence: str | None = Field(default=None, max_length=2_000)


class TopicFeedback(TopicModel):
    topic_id: str
    topic_version: int
    completion: Literal["completed", "interrupted"]
    learner_turn_count: int = Field(ge=0)
    checkpoints: list[TopicCheckpointResult] = Field(max_length=5)
    target_usage: list[TargetUsage] = Field(max_length=5)
    corrections: list[SpeakingCorrection] = Field(max_length=8)
    limitations: list[str] = Field(max_length=5)


class TopicAttemptFinishResponse(TopicModel):
    state: TopicAttemptState
    feedback: TopicFeedback
