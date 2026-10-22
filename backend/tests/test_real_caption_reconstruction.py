from pathlib import Path

from services.media.subtitles import parse_vtt_windows
from services.transcript.rolling_captions import (
    CaptionWindow,
    caption_windows_to_utterances,
    reconstruct_caption_transcript,
    reconstruct_caption_windows,
)


FIXTURE = Path(__file__).parent / "fixtures" / "easy_german_screenshot_pattern.vtt"


def test_screenshot_derived_rolling_caption_pattern_has_no_display_revisions():
    """Provisional regression: structure is screenshot-derived, not source VTT."""
    utterances = caption_windows_to_utterances(parse_vtt_windows(FIXTURE))
    assert utterances == [
        {
            "text": "Wir machen heute ein Experiment bei Easy German.",
            "start": 0.2,
            "end": 2.5,
        },
        {
            "text": "Wir haben hier Deutschlernende der Easy German Summer School.",
            "start": 2.6,
            "end": 5.0,
        },
        {
            "text": "Auf der Straße haben wir deutsche Muttersprachler.",
            "start": 5.1,
            "end": 7.3,
        },
        {
            "text": (
                "Wir wollen herausfinden, was passiert, wenn man Angst hat, "
                "in der deutschen Sprache Fehler zu machen."
            ),
            "start": 7.4,
            "end": 12.7,
        },
        {
            "text": (
                "Was passiert bei Deutschlernenden und was denken eigentlich "
                "Muttersprachler darüber?"
            ),
            "start": 12.8,
            "end": 16.0,
        },
    ]


def test_fixture_exposes_reviewable_reconstruction_trace_and_timing_provenance():
    windows = parse_vtt_windows(FIXTURE)
    reconstruction = reconstruct_caption_windows(windows)
    actions = [decision.action for decision in reconstruction.decisions]
    assert actions.count("replace_active_revision") >= 5
    assert "preserve_ambiguous_content" not in actions
    assert reconstruction.quality_flags == ("estimated_timing",)
    first = reconstruction.tokens[0]
    assert first.text == "Wir" and first.timing_source == "source_inline"
    assert reconstruction.tokens[1].timing_source == "estimated"


def test_middle_revision_without_source_timing_is_preserved_and_flagged():
    transcript = reconstruct_caption_transcript([
        CaptionWindow(0, 2, "Wir lernen heute Deutsch", window_id=1),
        CaptionWindow(1, 3, "Wir lernen gemeinsam heute Deutsch", window_id=2),
    ])
    assert transcript.requires_review is True
    assert "ambiguous_revision" in transcript.quality_flags
    assert "gemeinsam" in " ".join(value["text"] for value in transcript.utterances)


def test_inline_timing_preserves_real_repeated_speech(tmp_path):
    path = tmp_path / "repetition.vtt"
    path.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.200\n"
        "<00:00:00.100><c>Ich</c> <00:00:00.400><c>glaube</c><00:00:01.000>\n\n"
        "00:00:00.900 --> 00:00:02.500\n"
        "<00:00:01.300><c>Ich</c> <00:00:01.600><c>glaube.</c><00:00:02.100>\n",
        encoding="utf-8",
    )
    assert [value["text"] for value in caption_windows_to_utterances(parse_vtt_windows(path))] == [
        "Ich glaube Ich glaube."
    ]


def test_spoken_hesitation_inside_one_line_is_not_deduplicated(tmp_path):
    path = tmp_path / "hesitation.vtt"
    path.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n"
        "<00:00:00.100><c>Ich, ich glaube das.</c>\n",
        encoding="utf-8",
    )
    assert [value["text"] for value in caption_windows_to_utterances(parse_vtt_windows(path))] == [
        "Ich, ich glaube das."
    ]


def test_long_utterance_is_flagged_without_arbitrary_splitting():
    value = " ".join(["weiter"] * 60)
    transcript = reconstruct_caption_transcript([CaptionWindow(0, 35, value)])
    assert len(transcript.utterances) == 1
    assert transcript.utterances[0]["text"] == value
    assert transcript.requires_review is True
    assert "excessive_utterance_duration" in transcript.quality_flags


def test_distant_repeated_phrase_is_not_a_carryover_quality_failure():
    transcript = reconstruct_caption_transcript([
        CaptionWindow(0, 1, "Wir gehen.", window_id=1),
        CaptionWindow(5, 6, "Wir gehen.", window_id=2),
    ])
    assert [value["text"] for value in transcript.utterances] == ["Wir gehen.", "Wir gehen."]
    assert transcript.requires_review is False
