import subprocess
from pathlib import Path

import pytest

from services.media import extractor as extractor_module
from services.media.extractor import (
    get_subtitle_languages,
    get_whisper_model,
    normalize_sentences,
    separate_vocals,
)


class SubtitleDownloader:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def extract_info(self, url, download):
        subtitle_path = Path(self.options["outtmpl"].replace("%(ext)s", "en.vtt"))
        subtitle_path.write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello.\n",
            encoding="utf-8",
        )
        return {"title": "Test lesson"}


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


def test_separate_vocals_preserves_other_job_outputs(monkeypatch, tmp_path):
    shared_output = tmp_path / "_demucs"
    other_job_marker = shared_output / "other-job" / "in-progress"
    other_job_marker.parent.mkdir(parents=True)
    other_job_marker.write_text("keep", encoding="utf-8")

    def fake_demucs(command, **kwargs):
        output_dir = Path(command[command.index("-o") + 1])
        source_name = Path(command[-1]).stem
        vocals = output_dir / "model" / source_name / "vocals.mp3"
        vocals.parent.mkdir(parents=True)
        vocals.write_bytes(b"vocals")

    monkeypatch.setattr(extractor_module, "MEDIA", tmp_path)
    monkeypatch.setattr(extractor_module.subprocess, "run", fake_demucs)

    result = Path(separate_vocals(str(tmp_path / "lesson.mp3")))

    assert result.read_bytes() == b"vocals"
    assert other_job_marker.read_text(encoding="utf-8") == "keep"


def test_separate_vocals_cleans_partial_output_after_failure(monkeypatch, tmp_path):
    def fail_demucs(command, **kwargs):
        output_dir = Path(command[command.index("-o") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "partial.tmp").write_text("partial", encoding="utf-8")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(extractor_module, "MEDIA", tmp_path)
    monkeypatch.setattr(extractor_module.subprocess, "run", fail_demucs)

    with pytest.raises(subprocess.CalledProcessError):
        separate_vocals(str(tmp_path / "lesson.mp3"))

    assert list(tmp_path.rglob("partial.tmp")) == []


def test_extract_media_cleans_downloaded_subtitles(monkeypatch, tmp_path):
    monkeypatch.setattr(extractor_module, "MEDIA", tmp_path)
    monkeypatch.setattr(extractor_module, "YoutubeDL", SubtitleDownloader)

    result = extractor_module.extract_media(
        "https://example.invalid/lesson", None, None
    )

    assert result["source"] == "subtitles"
    assert result["sentences"][0]["text"] == "Hello."
    assert list(tmp_path.glob("*.vtt")) == []


def test_extract_media_cleans_downloaded_subtitles_after_parse_failure(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(extractor_module, "MEDIA", tmp_path)
    monkeypatch.setattr(extractor_module, "YoutubeDL", SubtitleDownloader)
    monkeypatch.setattr(
        extractor_module,
        "parse_vtt",
        lambda path: (_ for _ in ()).throw(ValueError("invalid subtitles")),
    )

    with pytest.raises(ValueError, match="invalid subtitles"):
        extractor_module.extract_media("https://example.invalid/lesson", None, None)

    assert list(tmp_path.glob("*.vtt")) == []
