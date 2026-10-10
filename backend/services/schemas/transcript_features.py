from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TranscriptQualityFlag = Literal[
    "insufficient_transcript",
    "insufficient_timing",
    "excessive_overlap",
    "sparse_timing",
    "duplicated_cues",
    "malformed_cues",
]


class TranscriptLearningFeatures(BaseModel):
    """Objective statistics derived only from transcript text and timestamps."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    duration_seconds: float = Field(ge=0)
    active_duration_seconds: float = Field(ge=0)
    word_count: int = Field(ge=0)
    words_per_minute: float = Field(ge=0)
    cue_count: int = Field(ge=0)
    cues_per_minute: float = Field(ge=0)
    average_words_per_cue: float = Field(ge=0)
    median_words_per_cue: float = Field(ge=0)
    lexical_diversity: float = Field(ge=0, le=1)
    repeated_token_ratio: float = Field(ge=0, le=1)
    question_cue_ratio: float = Field(ge=0, le=1)
    short_cue_ratio: float = Field(ge=0, le=1)
    long_cue_ratio: float = Field(ge=0, le=1)
    median_gap_seconds: float = Field(ge=0)
    long_gap_ratio: float = Field(ge=0, le=1)
    data_quality_flags: tuple[TranscriptQualityFlag, ...] = ()
