from types import SimpleNamespace

from services.media.subtitles import parse_vtt_windows
from services.transcript.rolling_captions import cues_to_utterances
from services.transcript.utterances import whisper_words_to_utterances


def texts(cues):
    return [value["text"] for value in cues_to_utterances(cues)]


def test_exact_overlapping_revision_is_removed():
    assert texts([(0, 2, "Hello there."), (1, 2.5, "Hello there.")]) == ["Hello there."]


def test_cumulative_caption_emits_only_novel_suffix():
    assert texts([(0, 2, "To take away to go?"), (2, 4, "To take away to go? Ah, yes.")]) == [
        "To take away to go?", "Ah, yes."
    ]


def test_suffix_prefix_overlap_is_deduplicated():
    assert texts([(0, 2, "We should go"), (1.5, 3, "should go now.")]) == ["We should go now."]


def test_temporally_distant_repetition_is_retained():
    assert texts([(0, 1, "Go."), (5, 6, "Go.")]) == ["Go.", "Go."]


def test_adjacent_intentional_single_word_repetition_is_retained():
    assert texts([(0, 1, "No"), (1, 2, "No.")]) == ["No No."]


def test_punctuation_pause_and_tail_boundaries():
    assert texts([(0, 1, "Bonjour. Fantastique."), (3, 4, "unfinished tail")]) == [
        "Bonjour.", "Fantastique.", "unfinished tail"
    ]


def test_long_pause_splits_but_tiny_pause_does_not():
    assert texts([(0, 1, "first part"), (2, 3, "second part")]) == ["first part", "second part"]
    assert texts([(0, 1, "first part"), (1.2, 2, "second part")]) == ["first part second part"]


def test_long_text_is_not_split_at_160_characters_without_natural_boundary():
    value = " ".join(["word"] * 50)
    result = cues_to_utterances([(0, 8, value)])
    assert len(result) == 1 and result[0]["text"] == value


def test_music_is_removed_without_deleting_lexical_short_utterances():
    assert texts([(0, 1, "[musik]"), (1, 2, "[singen][music]"), (2, 3, "Ja.")]) == ["Ja."]
    assert texts([(0, 1, "Hallo [music] Welt.")]) == ["Hallo Welt."]


def word(value, start, end):
    return SimpleNamespace(word=value, start=start, end=end)


def segment(text, start, end, words):
    return SimpleNamespace(text=text, start=start, end=end, words=words)


def test_whisper_segments_converge_on_same_utterance_pipeline_and_keep_timing():
    segments = [
        segment("Hello", 0, 1, [word(" Hello", 0.1, 0.5)]),
        segment("there.", 1, 2, [word(" there.", 0.6, 1.2)]),
        segment("Next. Again.", 2, 4, [word(" Next.", 2, 2.5), word(" Again.", 2.6, 3.2)]),
    ]
    assert whisper_words_to_utterances(segments) == [
        {"text": "Hello there.", "start": 0.1, "end": 1.2},
        {"text": "Next.", "start": 2.0, "end": 2.5},
        {"text": "Again.", "start": 2.6, "end": 3.2},
    ]


def test_whisper_segment_without_word_timestamps_is_not_dropped():
    segments = [
        segment("Hello", 0, 1, [word(" Hello", 0.1, 0.5)]),
        segment("there.", 1, 2, []),
    ]
    assert whisper_words_to_utterances(segments) == [
        {"text": "Hello there.", "start": 0.1, "end": 2.0},
    ]


def test_vtt_inline_timestamps_are_preserved_as_word_boundaries(tmp_path):
    path = tmp_path / "captions.vtt"
    path.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n"
        "<00:00:01.200><c>Hello</c> <00:00:02.100><c>there.</c>\n",
        encoding="utf-8",
    )
    windows = parse_vtt_windows(path)
    assert [(token.text, token.start, token.end) for token in windows[0].tokens] == [
        ("Hello", 1.2, 2.1),
        ("there", 2.1, 2.55),
        (".", 2.55, 3.0),
    ]


def test_explicit_vtt_voice_change_is_an_utterance_boundary(tmp_path):
    path = tmp_path / "speakers.vtt"
    path.write_text(
        "WEBVTT\r\n\r\n00:00:00.000 --> 00:00:01.000\r\n"
        "<v Alice>Hello there\r\n\r\n"
        "00:00:01.100 --> 00:00:02.000\r\n<v Bob>Hi Alice\r\n",
        encoding="utf-8",
    )
    from services.transcript.rolling_captions import caption_windows_to_utterances

    assert [value["text"] for value in caption_windows_to_utterances(parse_vtt_windows(path))] == [
        "Hello there",
        "Hi Alice",
    ]
