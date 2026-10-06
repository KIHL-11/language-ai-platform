from services.media.extractor import (
    get_subtitle_languages,
    get_whisper_model,
    normalize_sentences,
)


def test_subtitle_language_priority_supports_english_and_german():
    assert get_subtitle_languages("en") == ["en", "en-US", "en-GB", "en-orig"]
    assert get_subtitle_languages("de") == ["de", "de-DE", "de-orig"]


def test_whisper_model_selection_uses_language_specific_environment(monkeypatch):
    monkeypatch.setenv("WHISPER_MODEL_EN", "tiny.en")
    monkeypatch.setenv("WHISPER_MODEL_MULTI", "small")

    assert get_whisper_model("en") == "tiny.en"
    assert get_whisper_model("de") == "small"


def test_normalize_sentences_assigns_ids_and_float_seconds():
    normalized = normalize_sentences(
        [{"text": "Hallo.", "start": 1, "end": 2.75}]
    )

    assert normalized == [{"id": 1, "text": "Hallo.", "start": 1.0, "end": 2.75}]
