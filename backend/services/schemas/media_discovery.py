from typing import Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from services.schemas.lesson import SourceLanguage
from services.schemas.transcript_features import TranscriptLearningFeatures


MediaAvailability = Literal["public", "private", "unavailable", "unknown"]
SubtitleEnrichmentStatus = Literal[
    "success", "unavailable", "failed", "rate_limited"
]
SubtitleKind = Literal["human", "automatic"]
TrainingGoal = Literal[
    "general",
    "blind_listening",
    "comprehension",
    "mini_dictation",
    "shadowing",
    "retell",
]
SCORE_COMPONENT_MAXIMA = {
    "subtitle_quality": 30,
    "query_relevance": 30,
    "duration_fit": 20,
    "training_goal_fit": 20,
}
DEFAULT_ENRICHMENT_LIMIT = 2
MAX_ENRICHMENT_LIMIT = 3


class DiscoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MediaDiscoveryRequest(DiscoveryModel):
    query: str = Field(min_length=1, max_length=200)
    target_language: SourceLanguage = "en"
    min_duration_seconds: int = Field(default=60, ge=0, le=14_400)
    max_duration_seconds: int = Field(default=1_800, ge=1, le=14_400)
    limit: int = Field(default=5, ge=1, le=10)
    training_goal: TrainingGoal = "general"
    enrich_transcript: bool = False
    enrichment_limit: int = Field(
        default=DEFAULT_ENRICHMENT_LIMIT, ge=1, le=MAX_ENRICHMENT_LIMIT
    )

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        printable = "".join(character if character.isprintable() else " " for character in value)
        normalized = " ".join(printable.split())
        if not normalized:
            raise ValueError("query must contain non-whitespace characters")
        return normalized

    @model_validator(mode="after")
    def validate_duration_range(self):
        if self.min_duration_seconds > self.max_duration_seconds:
            raise ValueError(
                "min_duration_seconds must be less than or equal to "
                "max_duration_seconds"
            )
        return self


class SubtitleTrackSummary(DiscoveryModel):
    language: str = Field(min_length=1, max_length=32)
    has_human: bool = False
    has_automatic: bool = False


class LearningSuitabilityBreakdown(DiscoveryModel):
    subtitle_quality: float = Field(
        ge=0, le=SCORE_COMPONENT_MAXIMA["subtitle_quality"]
    )
    query_relevance: float = Field(
        ge=0, le=SCORE_COMPONENT_MAXIMA["query_relevance"]
    )
    duration_fit: float = Field(
        ge=0, le=SCORE_COMPONENT_MAXIMA["duration_fit"]
    )
    training_goal_fit: float = Field(
        ge=0, le=SCORE_COMPONENT_MAXIMA["training_goal_fit"]
    )


class LearningSuitabilityScore(DiscoveryModel):
    total: float = Field(ge=0, le=100)
    breakdown: LearningSuitabilityBreakdown
    reasons: list[str] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_component_total(self):
        component_total = round(sum(self.breakdown.model_dump().values()), 2)
        if self.total != component_total:
            raise ValueError("total must equal the sum of suitability components")
        return self


class TranscriptEnrichment(DiscoveryModel):
    status: SubtitleEnrichmentStatus
    subtitle_kind: SubtitleKind | None = None
    features: TranscriptLearningFeatures | None = None

    @model_validator(mode="after")
    def validate_success_payload(self):
        has_payload = self.subtitle_kind is not None and self.features is not None
        if self.status == "success" and not has_payload:
            raise ValueError("successful enrichment requires kind and features")
        if self.status != "success" and (
            self.subtitle_kind is not None or self.features is not None
        ):
            raise ValueError("unsuccessful enrichment cannot include payload")
        return self


class MediaCandidate(DiscoveryModel):
    provider: Literal["youtube"]
    provider_id: str = Field(min_length=1, max_length=64)
    url: AnyHttpUrl
    title: str = Field(min_length=1, max_length=300)
    channel: str | None = Field(default=None, max_length=200)
    duration_seconds: float = Field(ge=0, le=86_400)
    thumbnail: AnyHttpUrl | None = None
    is_live: bool = False
    availability: MediaAvailability = "unknown"
    subtitle_tracks: list[SubtitleTrackSummary] = Field(max_length=100)
    suitability: LearningSuitabilityScore
    transcript_enrichment: TranscriptEnrichment | None = None
