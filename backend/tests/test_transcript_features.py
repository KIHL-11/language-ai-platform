import math

from services.media.transcript_features import (
    LONG_CUE_MIN_WORDS,
    LONG_GAP_SECONDS,
    SHORT_CUE_MAX_WORDS,
    extract_transcript_learning_features,
)


def cue(start, end, text):
    return {"start": start, "end": end, "text": text}


def test_normal_english_transcript_produces_objective_features():
    features = extract_transcript_learning_features(
        [
            cue(0, 2, "Welcome to this practical English lesson today."),
            cue(2.5, 5, "We will compare several useful phrases and examples."),
            cue(5.5, 8, "Which expression would you choose in this situation?"),
        ]
    )

    assert features.cue_count == 3
    assert features.word_count == 23
    assert features.duration_seconds == 8
    assert features.active_duration_seconds == 7
    assert features.question_cue_ratio == 1 / 3
    assert features.data_quality_flags == ()


def test_normal_german_transcript_preserves_unicode_words():
    features = extract_transcript_learning_features(
        [cue(0, 2, "Schöne Grüße aus München."), cue(2, 4, "Wie heißt du?")]
    )

    assert features.word_count == 7
    assert features.lexical_diversity == 1
    assert features.question_cue_ratio == 0.5


def test_empty_transcript_returns_finite_zero_features_and_quality_flags():
    features = extract_transcript_learning_features([])

    assert features.cue_count == 0
    assert features.word_count == 0
    assert features.duration_seconds == 0
    assert features.active_duration_seconds == 0
    assert features.words_per_minute == 0
    assert features.data_quality_flags == (
        "insufficient_transcript",
        "insufficient_timing",
    )


def test_single_cue_uses_its_timed_interval():
    features = extract_transcript_learning_features([cue(10, 12, "one two three")])

    assert features.duration_seconds == 2
    assert features.active_duration_seconds == 2
    assert features.words_per_minute == 90
    assert features.cues_per_minute == 30
    assert features.median_gap_seconds == 0


def test_overlapping_cues_use_interval_union_and_nonnegative_gaps():
    features = extract_transcript_learning_features(
        [cue(0, 4, "one two three four"), cue(2, 6, "five six seven eight")]
    )

    assert features.duration_seconds == 6
    assert features.active_duration_seconds == 6
    assert features.median_gap_seconds == 0
    assert features.long_gap_ratio == 0
    assert "excessive_overlap" in features.data_quality_flags


def test_adjacent_duplicate_cues_are_removed_before_feature_calculation():
    features = extract_transcript_learning_features(
        [
            cue(0, 1, "Hello   world"),
            cue(1, 2, "<i>Hello world</i>"),
            cue(2, 3, "A different cue"),
        ]
    )

    assert features.cue_count == 2
    assert features.word_count == 5
    assert features.active_duration_seconds == 3
    assert "duplicated_cues" in features.data_quality_flags


def test_distant_repeated_text_is_retained_as_legitimate_content():
    features = extract_transcript_learning_features(
        [cue(0, 1, "Again"), cue(10, 11, "Again")]
    )

    assert features.cue_count == 2
    assert features.word_count == 2
    assert features.active_duration_seconds == 2
    assert "duplicated_cues" not in features.data_quality_flags


def test_large_timing_gap_does_not_count_as_active_transcript_time():
    features = extract_transcript_learning_features(
        [cue(0, 1, "one two three"), cue(11, 12, "four five")]
    )

    assert features.duration_seconds == 12
    assert features.active_duration_seconds == 2
    assert features.words_per_minute == 150
    assert features.median_gap_seconds == 10
    assert features.long_gap_ratio == 1
    assert "sparse_timing" in features.data_quality_flags


def test_malformed_timestamps_are_excluded_and_flagged():
    features = extract_transcript_learning_features(
        [
            cue("bad", 1, "invalid"),
            cue(-1, 1, "negative"),
            cue(3, 2, "backwards"),
            cue(float("nan"), 5, "not finite"),
            cue(0, 1, "valid cue"),
        ]
    )

    assert features.cue_count == 1
    assert features.word_count == 2
    assert features.data_quality_flags == (
        "insufficient_transcript",
        "malformed_cues",
    )


def test_punctuation_heavy_text_counts_words_without_claiming_difficulty():
    features = extract_transcript_learning_features(
        [cue(0, 2, "Wait... what?! Really—yes; absolutely!!!")]
    )

    assert features.word_count == 5
    assert features.question_cue_ratio == 1


def test_unicode_tokenization_is_not_limited_to_ascii():
    features = extract_transcript_learning_features(
        [cue(0, 2, "Grüße café naïve 東京")]
    )

    assert features.word_count == 4
    assert features.lexical_diversity == 1


def test_common_subtitle_formatting_tags_are_removed_conservatively():
    features = extract_transcript_learning_features(
        [cue(0, 2, "<v Alice><b>Hello</b> &amp; <i>welcome</i></v>")]
    )

    assert features.word_count == 2


def test_words_per_minute_uses_union_of_active_intervals():
    features = extract_transcript_learning_features(
        [
            cue(0, 1, "one two three four five"),
            cue(101, 102, "six seven eight nine ten"),
        ]
    )

    assert features.duration_seconds == 102
    assert features.active_duration_seconds == 2
    assert features.words_per_minute == 300


def test_lexical_diversity_is_unique_normalized_tokens_over_all_tokens():
    features = extract_transcript_learning_features(
        [cue(0, 1, "Hallo, HALLO welt schön")]
    )

    assert features.lexical_diversity == 0.75


def test_repeated_token_ratio_counts_occurrences_of_repeated_tokens():
    features = extract_transcript_learning_features(
        [cue(0, 1, "echo echo once twice twice twice")]
    )

    assert features.repeated_token_ratio == 5 / 6


def test_short_and_long_cue_ratios_use_centralized_inclusive_thresholds():
    short = " ".join(f"s{index}" for index in range(SHORT_CUE_MAX_WORDS))
    middle = " ".join(f"m{index}" for index in range(SHORT_CUE_MAX_WORDS + 1))
    long = " ".join(f"l{index}" for index in range(LONG_CUE_MIN_WORDS))
    features = extract_transcript_learning_features(
        [cue(0, 1, short), cue(1, 2, middle), cue(2, 3, long)]
    )

    assert features.short_cue_ratio == 1 / 3
    assert features.long_cue_ratio == 1 / 3


def test_question_ratio_recognizes_ascii_and_fullwidth_question_marks():
    features = extract_transcript_learning_features(
        [
            cue(0, 1, "Ready?"),
            cue(1, 2, "Bereit？"),
            cue(2, 3, "Ready."),
            cue(3, 4, "Yes!"),
        ]
    )

    assert features.question_cue_ratio == 0.5


def test_gap_statistics_use_zero_for_overlap_and_documented_long_threshold():
    features = extract_transcript_learning_features(
        [
            cue(0, 1, "a"),
            cue(2, 3, "b"),
            cue(6, 7, "c"),
            cue(13, 14, "d"),
        ]
    )

    assert LONG_GAP_SECONDS == 2
    assert features.median_gap_seconds == 3
    assert features.long_gap_ratio == 2 / 3


def test_quality_flags_report_low_volume_overlap_sparsity_and_duplicates():
    features = extract_transcript_learning_features(
        [
            cue(0, 4, "same"),
            cue(1, 2, "same"),
            cue(10, 11, "different"),
        ]
    )

    assert features.data_quality_flags == (
        "insufficient_transcript",
        "excessive_overlap",
        "sparse_timing",
        "duplicated_cues",
    )


def test_repeated_runs_are_deterministic():
    cues = [cue(3, 4, "Second cue"), cue(0, 2, "First cue?")]

    first = extract_transcript_learning_features(cues)
    second = extract_transcript_learning_features(cues)

    assert first == second
    assert first.model_dump() == second.model_dump()


def test_extreme_values_never_produce_nan_or_infinity():
    features = extract_transcript_learning_features(
        [
            cue(0, float("inf"), "infinite"),
            cue(0, 1e300, "extreme"),
            cue(0, 1e-300, "extreme tiny interval"),
        ]
    )

    numeric_values = [
        value
        for value in features.model_dump().values()
        if isinstance(value, (int, float))
    ]
    assert all(math.isfinite(value) for value in numeric_values)
    assert "malformed_cues" in features.data_quality_flags


def test_empty_text_and_non_mapping_entries_are_ignored_without_mutating_input():
    cues = [cue(0, 1, "   "), None, cue(1, 2, "kept")]
    original = [dict(cues[0]), None, dict(cues[2])]

    features = extract_transcript_learning_features(cues)

    assert features.cue_count == 1
    assert features.word_count == 1
    assert "malformed_cues" in features.data_quality_flags
    assert cues == original
