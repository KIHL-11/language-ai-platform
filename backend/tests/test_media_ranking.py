import pytest
from pydantic import ValidationError

from services.media.discovery import MediaDiscoveryService, VideoSearchResult
from services.schemas.media_discovery import (
    MediaDiscoveryRequest,
    SCORE_COMPONENT_MAXIMA,
    SubtitleTrackSummary,
)


class FakeProvider:
    def __init__(self, results):
        self.results = results

    def search(self, query, limit):
        return self.results


def result(
    provider_id,
    *,
    title="English practice",
    channel="Learning channel",
    duration=600,
    human=True,
    automatic=False,
):
    return VideoSearchResult(
        provider="youtube",
        provider_id=provider_id,
        url=f"https://www.youtube.com/watch?v={provider_id}",
        title=title,
        channel=channel,
        duration_seconds=duration,
        thumbnail=None,
        is_live=False,
        availability="public",
        subtitle_tracks=[
            SubtitleTrackSummary(
                language="en",
                has_human=human,
                has_automatic=automatic,
            )
        ],
    )


def discover(results, **overrides):
    values = {
        "query": "English practice",
        "target_language": "en",
        "min_duration_seconds": 60,
        "max_duration_seconds": 1800,
        "limit": 10,
    }
    values.update(overrides)
    request = MediaDiscoveryRequest(**values)
    return MediaDiscoveryService(FakeProvider(results)).discover(request)


def test_existing_request_defaults_to_general_training_goal():
    request = MediaDiscoveryRequest(query="English practice")

    assert request.training_goal == "general"


@pytest.mark.parametrize(
    "unsupported_goal", ["active_recall", "live_dialogue", "unknown"]
)
def test_request_rejects_unsupported_training_goals(unsupported_goal):
    with pytest.raises(ValidationError):
        MediaDiscoveryRequest(
            query="English practice", training_goal=unsupported_goal
        )


def test_general_keeps_human_captions_above_automatic_when_other_signals_equal():
    candidates = discover(
        [
            result("auto", human=False, automatic=True),
            result("human", human=True),
        ]
    )

    assert [candidate.provider_id for candidate in candidates] == ["human", "auto"]


def test_shadowing_prefers_goal_sized_human_caption_content():
    candidates = discover(
        [
            result("long", duration=1500),
            result("fit", duration=420),
        ],
        training_goal="shadowing",
    )

    assert [candidate.provider_id for candidate in candidates] == ["fit", "long"]
    assert "shadowing" in candidates[0].suitability.reasons[-1].casefold()


def test_mini_dictation_prefers_shorter_suitable_content():
    candidates = discover(
        [result("long", duration=900), result("short", duration=180)],
        training_goal="mini_dictation",
    )

    assert [candidate.provider_id for candidate in candidates] == ["short", "long"]


def test_comprehension_can_prefer_moderately_longer_content():
    candidates = discover(
        [result("short", duration=180), result("longer", duration=1200)],
        training_goal="comprehension",
    )

    assert [candidate.provider_id for candidate in candidates] == ["longer", "short"]


def test_retell_penalizes_extremely_short_content():
    candidates = discover(
        [result("tiny", duration=60), result("context", duration=600)],
        training_goal="retell",
    )

    assert [candidate.provider_id for candidate in candidates] == ["context", "tiny"]
    assert (
        candidates[0].suitability.breakdown.training_goal_fit
        > candidates[1].suitability.breakdown.training_goal_fit
    )


def test_query_relevance_normalizes_unicode_case_punctuation_and_duplicates():
    candidates = discover(
        [
            result("none", title="Unrelated topic", channel="Other"),
            result("match", title="Cafe\u0301 LESSON", channel="Teacher"),
        ],
        query="CAFÉ, café... lesson!!!",
    )

    assert [candidate.provider_id for candidate in candidates] == ["match", "none"]
    assert candidates[0].suitability.breakdown.query_relevance == 30


def test_equal_candidate_order_is_independent_from_provider_result_order():
    candidates = [
        result("z-id", title="Same", duration=600),
        result("a-id", title="Same", duration=600),
    ]

    forward = discover(candidates)
    reversed_results = discover(list(reversed(candidates)))

    assert [candidate.provider_id for candidate in forward] == ["a-id", "z-id"]
    assert [candidate.provider_id for candidate in reversed_results] == [
        "a-id",
        "z-id",
    ]


@pytest.mark.parametrize(
    "training_goal",
    [
        "general",
        "blind_listening",
        "comprehension",
        "mini_dictation",
        "shadowing",
        "retell",
    ],
)
def test_scores_and_components_stay_in_bounds_and_sum_exactly(training_goal):
    candidate = discover(
        [result("bounded", duration=600)], training_goal=training_goal
    )[0]
    breakdown = candidate.suitability.breakdown.model_dump()

    assert 0 <= candidate.suitability.total <= 100
    assert sum(SCORE_COMPONENT_MAXIMA.values()) == 100
    for name, value in breakdown.items():
        assert 0 <= value <= SCORE_COMPONENT_MAXIMA[name]
    assert candidate.suitability.total == round(sum(breakdown.values()), 2)


def test_reasons_are_bounded_observable_and_avoid_unsupported_claims():
    candidate = discover(
        [result("explain", duration=420)], training_goal="shadowing"
    )[0]
    reasons = " ".join(candidate.suitability.reasons).casefold()

    assert len(candidate.suitability.reasons) == 4
    assert "subtitle" in reasons
    assert "search topic" in reasons
    assert "duration" in reasons
    assert "shadowing" in reasons
    for unsupported in (
        "cefr",
        "speech rate",
        "accent",
        "pronunciation clarity",
        "dialogue density",
        "vocabulary difficulty",
        "grammar difficulty",
        "speaker count",
    ):
        assert unsupported not in reasons
