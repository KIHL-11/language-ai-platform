import pytest

from services.media.discovery import MediaDiscoveryService, VideoSearchResult
from services.media.subtitle_enrichment import (
    NormalizedTranscriptCue,
    SubtitleEnrichmentResult,
)
from services.media.transcript_ranking import (
    MAX_TRANSCRIPT_ADJUSTMENT,
    apply_transcript_ranking,
)
from services.schemas.media_discovery import (
    LearningSuitabilityBreakdown,
    LearningSuitabilityScore,
    MediaDiscoveryRequest,
    SubtitleTrackSummary,
)
from services.schemas.transcript_features import TranscriptLearningFeatures


def metadata_score(total=50):
    remaining = total
    values = []
    for maximum in (30, 30, 20, 20):
        value = min(remaining, maximum)
        values.append(value)
        remaining -= value
    breakdown = LearningSuitabilityBreakdown(
        subtitle_quality=values[0],
        query_relevance=values[1],
        duration_fit=values[2],
        training_goal_fit=values[3],
    )
    return LearningSuitabilityScore(
        total=total,
        metadata_total=total,
        transcript_adjustment=0,
        breakdown=breakdown,
        reasons=["subtitle", "query", "duration", "goal"],
    )


def features(**overrides):
    values = {
        "duration_seconds": 120,
        "active_duration_seconds": 110,
        "word_count": 275,
        "words_per_minute": 150,
        "cue_count": 40,
        "cues_per_minute": 20,
        "average_words_per_cue": 7,
        "median_words_per_cue": 7,
        "lexical_diversity": 0.6,
        "repeated_token_ratio": 0.5,
        "question_cue_ratio": 0.2,
        "short_cue_ratio": 0.2,
        "long_cue_ratio": 0.05,
        "median_gap_seconds": 0.5,
        "long_gap_ratio": 0.1,
        "data_quality_flags": (),
    }
    values.update(overrides)
    return TranscriptLearningFeatures(**values)


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
def test_each_training_goal_has_deterministic_transcript_heuristics(training_goal):
    selected = features()
    if training_goal == "mini_dictation":
        selected = features(
            words_per_minute=110,
            average_words_per_cue=5,
            median_words_per_cue=5,
            short_cue_ratio=0.5,
        )

    score = apply_transcript_ranking(
        metadata_score(), selected, training_goal
    )

    assert score.transcript_adjustment > 0
    assert score.total > score.metadata_total
    assert training_goal.replace("_", " ") in score.reasons[-1].casefold()


def test_high_wpm_scores_lower_than_moderate_wpm_for_shadowing():
    moderate = apply_transcript_ranking(
        metadata_score(), features(words_per_minute=140), "shadowing"
    )
    high = apply_transcript_ranking(
        metadata_score(), features(words_per_minute=320), "shadowing"
    )

    assert high.transcript_adjustment < moderate.transcript_adjustment
    assert "pacing" in high.reasons[-1].casefold()


def test_low_wpm_scores_lower_than_moderate_wpm_for_blind_listening():
    moderate = apply_transcript_ranking(
        metadata_score(), features(words_per_minute=140), "blind_listening"
    )
    low = apply_transcript_ranking(
        metadata_score(), features(words_per_minute=20), "blind_listening"
    )

    assert low.transcript_adjustment < moderate.transcript_adjustment


def test_short_cues_outscore_long_cues_for_mini_dictation():
    short = apply_transcript_ranking(
        metadata_score(),
        features(
            words_per_minute=110,
            average_words_per_cue=4,
            median_words_per_cue=4,
            short_cue_ratio=0.6,
            long_cue_ratio=0,
        ),
        "mini_dictation",
    )
    long = apply_transcript_ranking(
        metadata_score(),
        features(
            words_per_minute=110,
            average_words_per_cue=20,
            median_words_per_cue=20,
            short_cue_ratio=0,
            long_cue_ratio=0.8,
        ),
        "mini_dictation",
    )

    assert short.transcript_adjustment > long.transcript_adjustment
    assert "cue" in long.reasons[-1].casefold()


def test_sparse_timing_reduces_shadowing_adjustment():
    continuous = apply_transcript_ranking(
        metadata_score(), features(long_gap_ratio=0.05), "shadowing"
    )
    sparse = apply_transcript_ranking(
        metadata_score(),
        features(long_gap_ratio=0.8, data_quality_flags=("sparse_timing",)),
        "shadowing",
    )

    assert sparse.transcript_adjustment < continuous.transcript_adjustment
    assert "sparse" in sparse.reasons[-1].casefold()


@pytest.mark.parametrize("critical_flag", ["insufficient_timing", "excessive_overlap"])
def test_critical_timing_quality_retains_metadata_score(critical_flag):
    score = apply_transcript_ranking(
        metadata_score(),
        features(data_quality_flags=(critical_flag,)),
        "comprehension",
    )

    assert score.total == score.metadata_total == 50
    assert score.transcript_adjustment == 0
    assert "metadata ranking was retained" in score.reasons[-1]


def test_malformed_and_insufficient_transcript_is_critical_fallback():
    score = apply_transcript_ranking(
        metadata_score(),
        features(
            word_count=5,
            cue_count=1,
            data_quality_flags=("insufficient_transcript", "malformed_cues"),
        ),
        "retell",
    )

    assert score.transcript_adjustment == 0
    assert score.total == 50


def test_insufficient_transcript_reduces_but_does_not_inflate_contribution():
    clean = apply_transcript_ranking(
        metadata_score(), features(), "comprehension"
    )
    degraded = apply_transcript_ranking(
        metadata_score(),
        features(data_quality_flags=("insufficient_transcript",)),
        "comprehension",
    )

    assert abs(degraded.transcript_adjustment) <= abs(
        clean.transcript_adjustment
    ) / 2
    assert "quality" in degraded.reasons[-1].casefold()


@pytest.mark.parametrize("metadata_total", [0, 3, 50, 97, 100])
def test_total_and_adjustment_are_always_bounded(metadata_total):
    score = apply_transcript_ranking(
        metadata_score(metadata_total), features(), "shadowing"
    )

    assert 0 <= score.total <= 100
    assert (
        -MAX_TRANSCRIPT_ADJUSTMENT
        <= score.transcript_adjustment
        <= MAX_TRANSCRIPT_ADJUSTMENT
    )
    assert score.total == round(
        score.metadata_total + score.transcript_adjustment, 2
    )


def test_reasons_describe_measured_transcript_properties_only():
    score = apply_transcript_ranking(
        metadata_score(), features(words_per_minute=310), "shadowing"
    )
    reason = score.reasons[-1].casefold()

    assert any(term in reason for term in ("pacing", "cue", "timing", "transcript"))
    for unsupported in (
        "cefr",
        "vocabulary difficulty",
        "advanced vocabulary",
        "grammar proficiency",
        "pronunciation",
        "accent",
        "clear speech",
        "learner comprehension",
    ):
        assert unsupported not in reason


def test_repeated_scoring_is_deterministic_and_does_not_mutate_metadata_score():
    original = metadata_score()
    first = apply_transcript_ranking(original, features(), "general")
    second = apply_transcript_ranking(original, features(), "general")

    assert first == second
    assert original == metadata_score()


class FakeSearchProvider:
    def __init__(self, results):
        self.results = results

    def search(self, query, limit):
        return self.results


class FakeEnrichmentProvider:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def enrich(self, candidate, target_language):
        self.calls.append(candidate.provider_id)
        return self.responses[candidate.provider_id]


def search_result(provider_id):
    return VideoSearchResult(
        provider="youtube",
        provider_id=provider_id,
        url=f"https://www.youtube.com/watch?v={provider_id}",
        title="Same lesson",
        channel="Teacher",
        duration_seconds=600,
        thumbnail=None,
        is_live=False,
        availability="public",
        subtitle_tracks=[
            SubtitleTrackSummary(
                language="en", has_human=True, has_automatic=False
            )
        ],
    )


def request(**overrides):
    values = {
        "query": "Same lesson",
        "target_language": "en",
        "min_duration_seconds": 60,
        "max_duration_seconds": 1200,
        "limit": 5,
        "training_goal": "shadowing",
    }
    values.update(overrides)
    return MediaDiscoveryRequest(**values)


def transcript(words_per_cue, seconds_per_cue):
    words = (
        "alpha beta gamma delta echo foxtrot golf hotel india juliet kilo lima "
        "mango november oscar papa quebec romeo sierra tango uniform victor "
        "whiskey xray yankee zulu"
    ).split()
    cues = []
    for index in range(3):
        text = " ".join(words[:words_per_cue])
        start = index * seconds_per_cue
        cues.append(
            NormalizedTranscriptCue(start, start + seconds_per_cue, text)
        )
    return SubtitleEnrichmentResult(
        status="success", subtitle_kind="human", cues=tuple(cues)
    )


def test_enrichment_disabled_is_value_equivalent_and_does_not_call_provider():
    results = [search_result("b"), search_result("a")]
    enricher = FakeEnrichmentProvider({})
    service = MediaDiscoveryService(FakeSearchProvider(results), enricher)
    baseline = MediaDiscoveryService(FakeSearchProvider(results)).discover(request())

    actual = service.discover(request(enrich_transcript=False))

    assert actual == baseline
    assert enricher.calls == []


@pytest.mark.parametrize("status", ["unavailable", "failed", "rate_limited"])
def test_non_success_enrichment_keeps_metadata_score(status):
    result = search_result("a")
    baseline = MediaDiscoveryService(FakeSearchProvider([result])).discover(
        request()
    )[0]
    enriched = MediaDiscoveryService(
        FakeSearchProvider([result]),
        FakeEnrichmentProvider(
            {"a": SubtitleEnrichmentResult(status=status)}
        ),
    ).discover(request(enrich_transcript=True))[0]

    assert enriched.suitability == baseline.suitability


def test_null_enrichment_keeps_metadata_score():
    result = search_result("a")
    baseline = MediaDiscoveryService(FakeSearchProvider([result])).discover(
        request()
    )[0]

    enriched = MediaDiscoveryService(FakeSearchProvider([result])).discover(
        request(enrich_transcript=True)
    )[0]

    assert enriched.suitability == baseline.suitability
    assert enriched.transcript_enrichment is None


def test_successful_transcript_can_reorder_candidates_but_keeps_metadata_totals():
    results = [
        search_result("a"),
        search_result("b"),
        search_result("c"),
        search_result("d"),
    ]
    baseline = MediaDiscoveryService(FakeSearchProvider(results)).discover(request())
    provider = FakeEnrichmentProvider(
        {
            "a": transcript(words_per_cue=25, seconds_per_cue=1),
            "b": transcript(words_per_cue=8, seconds_per_cue=4),
        }
    )

    enriched = MediaDiscoveryService(
        FakeSearchProvider(results), provider
    ).discover(request(enrich_transcript=True, enrichment_limit=2))

    assert [item.provider_id for item in baseline] == ["a", "b", "c", "d"]
    assert [item.provider_id for item in enriched] == ["b", "c", "d", "a"]
    baseline_totals = {
        item.provider_id: item.suitability.metadata_total for item in baseline
    }
    assert all(
        item.suitability.metadata_total == baseline_totals[item.provider_id]
        for item in enriched
    )
    assert [
        item.provider_id
        for item in enriched
        if item.transcript_enrichment is None
    ] == ["c", "d"]


def test_rate_limit_still_stops_enrichment_and_preserves_all_metadata_scores():
    results = [search_result("a"), search_result("b"), search_result("c")]
    baseline = MediaDiscoveryService(FakeSearchProvider(results)).discover(request())
    provider = FakeEnrichmentProvider(
        {
            "a": SubtitleEnrichmentResult(status="rate_limited"),
            "b": transcript(words_per_cue=8, seconds_per_cue=4),
        }
    )

    enriched = MediaDiscoveryService(
        FakeSearchProvider(results), provider
    ).discover(request(enrich_transcript=True))

    assert provider.calls == ["a"]
    assert [item.suitability for item in enriched] == [
        item.suitability for item in baseline
    ]
    assert enriched[0].transcript_enrichment.status == "rate_limited"
    assert all(item.transcript_enrichment is None for item in enriched[1:])
