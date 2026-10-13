from dataclasses import dataclass

from services.media.ranking import GOAL_LABELS
from services.schemas.media_discovery import (
    LearningSuitabilityScore,
    TrainingGoal,
)
from services.schemas.transcript_features import TranscriptLearningFeatures


MAX_TRANSCRIPT_ADJUSTMENT = 10.0
DEGRADED_QUALITY_MULTIPLIER = 0.5
EXTREME_LOW_RATIO = 0.5
EXTREME_HIGH_RATIO = 1.5
FREQUENT_LONG_CUE_RATIO = 0.4
PACING_FIT_POINTS = 4.0
PACING_NEAR_PENALTY = -2.0
PACING_EXTREME_PENALTY = -4.0
CUE_FIT_POINTS = 3.0
CUE_NEAR_PENALTY = -1.5
CUE_EXTREME_PENALTY = -3.0
GOAL_FIT_POINTS = 3.0
GOAL_PENALTY = -3.0
NEUTRAL_GOAL_POINTS = 1.5
CRITICAL_QUALITY_FLAGS = {"insufficient_timing", "excessive_overlap"}
DEGRADED_QUALITY_FLAGS = {
    "insufficient_transcript",
    "sparse_timing",
    "duplicated_cues",
    "malformed_cues",
}
MINI_DICTATION_LONG_CUE_LIMIT = 0.25
MINI_DICTATION_SHORT_CUE_PREFERENCE = 0.3
SHADOWING_MAX_CUES_PER_MINUTE = 30
RETELL_MIN_WORD_COUNT = 40
GOAL_LONG_GAP_LIMITS: dict[TrainingGoal, float] = {
    "general": 0.6,
    "blind_listening": 0.4,
    "comprehension": 0.6,
    "mini_dictation": 1.0,
    "shadowing": 0.35,
    "retell": 0.5,
}


@dataclass(frozen=True, slots=True)
class TranscriptGoalProfile:
    minimum_wpm: float
    maximum_wpm: float
    minimum_words_per_cue: float
    maximum_words_per_cue: float


# Product heuristics over measured transcript properties. These ranges are not
# CEFR estimates and do not claim to measure pronunciation or comprehension.
GOAL_PROFILES: dict[TrainingGoal, TranscriptGoalProfile] = {
    "general": TranscriptGoalProfile(70, 200, 3, 14),
    "blind_listening": TranscriptGoalProfile(100, 180, 3, 12),
    "comprehension": TranscriptGoalProfile(80, 170, 5, 14),
    "mini_dictation": TranscriptGoalProfile(70, 150, 2, 8),
    "shadowing": TranscriptGoalProfile(90, 170, 2, 10),
    "retell": TranscriptGoalProfile(80, 180, 4, 14),
}


@dataclass(frozen=True, slots=True)
class ScoredSignal:
    points: float
    reason: str


def _pacing_signal(
    features: TranscriptLearningFeatures,
    goal: TrainingGoal,
    profile: TranscriptGoalProfile,
) -> ScoredSignal:
    wpm = features.words_per_minute
    label = GOAL_LABELS[goal]
    if profile.minimum_wpm <= wpm <= profile.maximum_wpm:
        return ScoredSignal(PACING_FIT_POINTS, f"Transcript pacing fits {label}.")
    if (
        wpm < profile.minimum_wpm * EXTREME_LOW_RATIO
        or wpm > profile.maximum_wpm * EXTREME_HIGH_RATIO
    ):
        return ScoredSignal(
            PACING_EXTREME_PENALTY,
            f"Transcript pacing is outside the preferred range for {label}.",
        )
    return ScoredSignal(
        PACING_NEAR_PENALTY,
        f"Transcript pacing is near, but outside, the preferred range for {label}.",
    )


def _cue_signal(
    features: TranscriptLearningFeatures,
    goal: TrainingGoal,
    profile: TranscriptGoalProfile,
) -> ScoredSignal:
    cue_length = features.median_words_per_cue
    label = GOAL_LABELS[goal]
    if (
        profile.minimum_words_per_cue
        <= cue_length
        <= profile.maximum_words_per_cue
    ):
        return ScoredSignal(CUE_FIT_POINTS, f"Measured cue length fits {label}.")
    if (
        features.long_cue_ratio >= FREQUENT_LONG_CUE_RATIO
        or cue_length > profile.maximum_words_per_cue * EXTREME_HIGH_RATIO
    ):
        return ScoredSignal(CUE_EXTREME_PENALTY, f"Measured cues are long for {label}.")
    return ScoredSignal(
        CUE_NEAR_PENALTY,
        f"Measured cue length is outside the preferred range for {label}.",
    )


def _goal_signal(
    features: TranscriptLearningFeatures, goal: TrainingGoal
) -> ScoredSignal:
    label = GOAL_LABELS[goal]
    if goal == "mini_dictation":
        if features.long_cue_ratio >= MINI_DICTATION_LONG_CUE_LIMIT:
            return ScoredSignal(
                GOAL_PENALTY,
                f"Long transcript cues are frequent for {label}.",
            )
        if features.short_cue_ratio >= MINI_DICTATION_SHORT_CUE_PREFERENCE:
            return ScoredSignal(
                GOAL_FIT_POINTS,
                f"Short transcript cues are frequent for {label}.",
            )
        return ScoredSignal(
            NEUTRAL_GOAL_POINTS,
            f"Transcript cue distribution is usable for {label}.",
        )
    if goal == "shadowing":
        if features.long_gap_ratio >= GOAL_LONG_GAP_LIMITS[goal]:
            return ScoredSignal(
                GOAL_PENALTY, f"Transcript timing is sparse for {label}."
            )
        if features.cues_per_minute > SHADOWING_MAX_CUES_PER_MINUTE:
            return ScoredSignal(
                GOAL_PENALTY,
                f"Transcript cue changes are frequent for {label}.",
            )
        return ScoredSignal(
            GOAL_FIT_POINTS, f"Transcript timing continuity fits {label}."
        )
    if goal == "blind_listening":
        if features.long_gap_ratio >= GOAL_LONG_GAP_LIMITS[goal]:
            return ScoredSignal(
                GOAL_PENALTY, f"Transcript timing is sparse for {label}."
            )
        return ScoredSignal(
            GOAL_FIT_POINTS, f"Transcript timing continuity fits {label}."
        )
    if goal == "retell":
        if features.word_count < RETELL_MIN_WORD_COUNT:
            return ScoredSignal(
                GOAL_PENALTY, f"Transcript word count is limited for {label}."
            )
        if features.long_gap_ratio > GOAL_LONG_GAP_LIMITS[goal]:
            return ScoredSignal(
                GOAL_PENALTY, f"Transcript timing is sparse for {label}."
            )
        return ScoredSignal(GOAL_FIT_POINTS, f"Transcript word count supports {label}.")
    if goal == "comprehension":
        if features.long_gap_ratio >= GOAL_LONG_GAP_LIMITS[goal]:
            return ScoredSignal(
                GOAL_PENALTY, f"Transcript timing is sparse for {label}."
            )
        return ScoredSignal(
            GOAL_FIT_POINTS, f"Transcript timing continuity fits {label}."
        )
    if features.long_gap_ratio >= GOAL_LONG_GAP_LIMITS[goal]:
        return ScoredSignal(GOAL_PENALTY, f"Transcript timing is sparse for {label}.")
    return ScoredSignal(GOAL_FIT_POINTS, f"Measured transcript structure fits {label}.")


def _is_critical_quality(features: TranscriptLearningFeatures) -> bool:
    flags = set(features.data_quality_flags)
    return bool(flags & CRITICAL_QUALITY_FLAGS) or {
        "insufficient_transcript",
        "malformed_cues",
    }.issubset(flags)


def apply_transcript_ranking(
    metadata_score: LearningSuitabilityScore,
    features: TranscriptLearningFeatures,
    training_goal: TrainingGoal,
) -> LearningSuitabilityScore:
    """Return a new score with a bounded adjustment from local transcript data."""
    if _is_critical_quality(features):
        adjustment = 0.0
        reason = (
            "Transcript quality was insufficient, so metadata ranking was retained."
        )
    else:
        profile = GOAL_PROFILES[training_goal]
        signals = (
            _pacing_signal(features, training_goal, profile),
            _cue_signal(features, training_goal, profile),
            _goal_signal(features, training_goal),
        )
        raw_adjustment = max(
            -MAX_TRANSCRIPT_ADJUSTMENT,
            min(MAX_TRANSCRIPT_ADJUSTMENT, sum(signal.points for signal in signals)),
        )
        flags = set(features.data_quality_flags)
        most_relevant = min(signals, key=lambda signal: signal.points)
        if flags & DEGRADED_QUALITY_FLAGS:
            raw_adjustment *= DEGRADED_QUALITY_MULTIPLIER
            if "sparse_timing" in flags:
                reason = (
                    f"Transcript timing is sparse; quality flags reduced the "
                    f"{GOAL_LABELS[training_goal]} adjustment."
                )
            else:
                reason = (
                    f"Transcript quality flags reduced the "
                    f"{GOAL_LABELS[training_goal]} adjustment."
                )
        else:
            reason = (
                most_relevant.reason
                if most_relevant.points < 0
                else max(signals, key=lambda signal: signal.points).reason
            )
        unclamped_total = metadata_score.metadata_total + raw_adjustment
        final_total = round(max(0.0, min(100.0, unclamped_total)), 2)
        adjustment = round(final_total - metadata_score.metadata_total, 2)

    return LearningSuitabilityScore(
        total=round(metadata_score.metadata_total + adjustment, 2),
        metadata_total=metadata_score.metadata_total,
        transcript_adjustment=adjustment,
        breakdown=metadata_score.breakdown,
        reasons=[*metadata_score.reasons, reason],
    )
