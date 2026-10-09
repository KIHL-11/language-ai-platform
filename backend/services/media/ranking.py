import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, Protocol

from services.schemas.media_discovery import (
    LearningSuitabilityBreakdown,
    LearningSuitabilityScore,
    MediaDiscoveryRequest,
    SCORE_COMPONENT_MAXIMA,
    TrainingGoal,
)


CaptionKind = Literal["human", "automatic"]
SUBTITLE_QUALITY_SCORES: dict[CaptionKind, int] = {
    "human": 30,
    "automatic": 22,
}
CLOSE_QUERY_MATCH_RATIO = 0.75
CENTERED_DURATION_RATIO = 0.75


class RankableCandidate(Protocol):
    title: str
    channel: str | None
    duration_seconds: float | None


@dataclass(frozen=True, slots=True)
class GoalProfile:
    preferred_min_seconds: int
    preferred_max_seconds: int
    falloff_seconds: int
    duration_points: int
    human_caption_bonus: int = 0
    automatic_caption_bonus: int = 0


# These are product ranking heuristics over known duration metadata. They are not
# measurements of pedagogical quality, speech rate, clarity, or language level.
TRAINING_GOAL_PROFILES: dict[TrainingGoal, GoalProfile | None] = {
    "general": None,
    "blind_listening": GoalProfile(300, 900, 300, 20),
    "comprehension": GoalProfile(600, 1_800, 600, 20),
    "mini_dictation": GoalProfile(60, 300, 300, 20),
    "shadowing": GoalProfile(120, 600, 480, 16, 4, 2),
    "retell": GoalProfile(300, 1_200, 300, 20),
}

GOAL_LABELS: dict[TrainingGoal, str] = {
    "general": "general practice",
    "blind_listening": "blind listening practice",
    "comprehension": "comprehension practice",
    "mini_dictation": "mini dictation practice",
    "shadowing": "shadowing practice",
    "retell": "retell practice",
}

LANGUAGE_LABELS = {"en": "English", "de": "German"}


def _terms(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("_", " ")
    return set(re.findall(r"\w+", normalized, flags=re.UNICODE))


def _requested_duration_fit(
    duration: float, minimum: int, maximum: int
) -> float:
    component_max = SCORE_COMPONENT_MAXIMA["duration_fit"]
    if minimum == maximum:
        return float(component_max)
    midpoint = (minimum + maximum) / 2
    half_range = (maximum - minimum) / 2
    return max(
        0.0,
        component_max * (1 - abs(duration - midpoint) / half_range),
    )


def _profile_duration_ratio(duration: float, profile: GoalProfile) -> float:
    if profile.preferred_min_seconds <= duration <= profile.preferred_max_seconds:
        return 1.0
    if duration < profile.preferred_min_seconds:
        distance = profile.preferred_min_seconds - duration
    else:
        distance = duration - profile.preferred_max_seconds
    return max(0.0, 1 - distance / profile.falloff_seconds)


def _goal_fit(
    duration: float, goal: TrainingGoal, caption_kind: CaptionKind
) -> float:
    profile = TRAINING_GOAL_PROFILES[goal]
    if profile is None:
        return float(SCORE_COMPONENT_MAXIMA["training_goal_fit"])
    caption_bonus = (
        profile.human_caption_bonus
        if caption_kind == "human"
        else profile.automatic_caption_bonus
    )
    score = (
        profile.duration_points * _profile_duration_ratio(duration, profile)
        + caption_bonus
    )
    return min(float(SCORE_COMPONENT_MAXIMA["training_goal_fit"]), score)


def _format_range(minimum: int, maximum: int) -> str:
    if minimum % 60 == 0 and maximum % 60 == 0:
        return f"{minimum // 60}–{maximum // 60} minute"
    return f"{minimum}–{maximum} second"


def _subtitle_reason(language: str, caption_kind: CaptionKind) -> str:
    label = LANGUAGE_LABELS.get(language, language)
    if caption_kind == "human":
        return f"Human {label} subtitles available."
    return f"Automatic {label} captions available; human subtitles were not found."


def _query_reason(matches: int, total: int) -> str:
    ratio = matches / total if total else 0
    if ratio >= CLOSE_QUERY_MATCH_RATIO:
        return "Title or channel closely matches your search topic."
    if matches:
        return "Title or channel partially matches your search topic."
    return "Title and channel have no direct token match with your search topic."


def _duration_reason(
    duration_score: float, minimum: int, maximum: int
) -> str:
    requested_range = _format_range(minimum, maximum)
    if duration_score >= (
        SCORE_COMPONENT_MAXIMA["duration_fit"] * CENTERED_DURATION_RATIO
    ):
        return f"Duration is near the center of your requested {requested_range} range."
    return f"Duration is within your requested {requested_range} range."


def _goal_reason(duration: float, goal: TrainingGoal) -> str:
    profile = TRAINING_GOAL_PROFILES[goal]
    if profile is None:
        return "For this search, general practice uses your requested duration range."
    preferred_range = _format_range(
        profile.preferred_min_seconds, profile.preferred_max_seconds
    )
    label = GOAL_LABELS[goal]
    if profile.preferred_min_seconds <= duration <= profile.preferred_max_seconds:
        return (
            f"For this search, {label} favors the {preferred_range} range; "
            "this video fits it."
        )
    return (
        f"For this search, {label} favors the {preferred_range} range; "
        "this video is outside it."
    )


def score_candidate(
    candidate: RankableCandidate,
    request: MediaDiscoveryRequest,
    caption_kind: CaptionKind,
) -> LearningSuitabilityScore:
    duration = float(candidate.duration_seconds or 0)
    subtitle_quality = float(SUBTITLE_QUALITY_SCORES[caption_kind])

    query_terms = _terms(request.query)
    metadata_terms = _terms(f"{candidate.title} {candidate.channel or ''}")
    matches = len(query_terms & metadata_terms)
    query_relevance = (
        SCORE_COMPONENT_MAXIMA["query_relevance"] * matches / len(query_terms)
        if query_terms
        else 0.0
    )
    duration_fit = _requested_duration_fit(
        duration,
        request.min_duration_seconds,
        request.max_duration_seconds,
    )
    training_goal_fit = _goal_fit(
        duration, request.training_goal, caption_kind
    )

    breakdown = LearningSuitabilityBreakdown(
        subtitle_quality=round(subtitle_quality, 2),
        query_relevance=round(query_relevance, 2),
        duration_fit=round(duration_fit, 2),
        training_goal_fit=round(training_goal_fit, 2),
    )
    total = round(sum(breakdown.model_dump().values()), 2)
    return LearningSuitabilityScore(
        total=total,
        breakdown=breakdown,
        reasons=[
            _subtitle_reason(request.target_language, caption_kind),
            _query_reason(matches, len(query_terms)),
            _duration_reason(
                duration_fit,
                request.min_duration_seconds,
                request.max_duration_seconds,
            ),
            _goal_reason(duration, request.training_goal),
        ],
    )
