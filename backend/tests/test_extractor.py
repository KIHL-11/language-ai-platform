import subprocess
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from services.media import extractor as extractor_module
from services.media.extractor import (
    get_subtitle_languages,
    get_whisper_model,
    normalize_sentences,
    separate_vocals,
)


class SubtitleDownloader:
    calls = []

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def extract_info(self, url, download):
        self.__class__.calls.append((self.options.copy(), download))
        if self.options.get("writesubtitles"):
            subtitle_path = Path(
                self.options["outtmpl"].replace("%(ext)s", "en.vtt")
            )
            subtitle_path.write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello.\n",
                encoding="utf-8",
            )
        else:
            Path(self.options["outtmpl"].replace("%(ext)s", "mp3")).write_bytes(
                b"audio"
            )
        return {"title": "Test lesson"}


class SubtitleFailureThenMediaDownloader:
    calls = []

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def extract_info(self, url, download):
        self.__class__.calls.append((self.options.copy(), download))
        output = Path(self.options["outtmpl"])
        if self.options.get("writesubtitles"):
            Path(str(output).replace("%(ext)s", "en.vtt.part")).write_text(
                "partial subtitle", encoding="utf-8"
            )
            raise DownloadError(
                "Unable to download video subtitles for 'en': HTTP Error 429"
            )
        Path(str(output).replace("%(ext)s", "mp3")).write_bytes(b"audio")
        return {"title": "Fallback lesson"}


class SubtitleSuccessThenMediaFailureDownloader:
    calls = []

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def extract_info(self, url, download):
        self.__class__.calls.append((self.options.copy(), download))
        output = Path(self.options["outtmpl"])
        if self.options.get("writesubtitles"):
            Path(str(output).replace("%(ext)s", "en.vtt")).write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello.\n",
                encoding="utf-8",
            )
            return {"title": "Test lesson"}
        Path(str(output).replace("%(ext)s", "webm.part")).write_bytes(b"partial")
        raise DownloadError("Media download failed")


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
    SubtitleDownloader.calls = []
    monkeypatch.setattr(extractor_module, "MEDIA", tmp_path)
    monkeypatch.setattr(extractor_module, "YoutubeDL", SubtitleDownloader)
    monkeypatch.setattr(
        extractor_module,
        "whisper_transcribe",
        lambda path, language: pytest.fail("Whisper should not run for usable subtitles"),
    )

    result = extractor_module.extract_media(
        "https://example.invalid/lesson", None, None
    )

    assert result["source"] == "subtitles"
    assert result["sentences"][0]["text"] == "Hello."
    assert result["audio_url"].endswith(".mp3")
    assert list(tmp_path.glob("*.vtt")) == []
    assert sorted(path.suffix for path in tmp_path.iterdir()) == [".mp3"]
    assert len(SubtitleDownloader.calls) == 2
    subtitle_options, subtitle_download = SubtitleDownloader.calls[0]
    media_options, media_download = SubtitleDownloader.calls[1]
    assert subtitle_download is True
    assert subtitle_options["skip_download"] is True
    assert subtitle_options["retries"] == 0
    assert media_download is True
    assert "skip_download" not in media_options
    assert "writesubtitles" not in media_options
    assert "writeautomaticsub" not in media_options


def test_extract_media_cleans_downloaded_subtitles_after_parse_failure(
    monkeypatch, tmp_path
):
    SubtitleDownloader.calls = []
    monkeypatch.setattr(extractor_module, "MEDIA", tmp_path)
    monkeypatch.setattr(extractor_module, "YoutubeDL", SubtitleDownloader)
    monkeypatch.setattr(
        extractor_module,
        "parse_vtt",
        lambda path: (_ for _ in ()).throw(ValueError("invalid subtitles")),
    )

    with pytest.raises(ValueError, match="invalid subtitles"):
        extractor_module.extract_media("https://example.invalid/lesson", None, None)

    assert list(tmp_path.iterdir()) == []


def test_subtitle_download_error_continues_to_media_and_whisper(
    monkeypatch, tmp_path
):
    SubtitleFailureThenMediaDownloader.calls = []
    whisper_calls = []
    monkeypatch.setattr(extractor_module, "MEDIA", tmp_path)
    monkeypatch.setattr(extractor_module, "BACKEND_DIR", tmp_path / "backend")
    monkeypatch.delenv("YT_COOKIES_FILE", raising=False)
    monkeypatch.delenv("YT_COOKIES_BROWSER", raising=False)
    monkeypatch.setattr(
        extractor_module, "YoutubeDL", SubtitleFailureThenMediaDownloader
    )
    monkeypatch.setattr(
        extractor_module,
        "whisper_transcribe",
        lambda path, language: (
            whisper_calls.append((Path(path), language))
            or [{"text": "Fallback transcript.", "start": 0, "end": 1}]
        ),
    )

    result = extractor_module.extract_media(
        "https://example.invalid/lesson", None, None
    )

    assert result["source"] == "whisper"
    assert result["sentences"][0]["text"] == "Fallback transcript."
    assert len(whisper_calls) == 1
    assert whisper_calls[0][0].read_bytes() == b"audio"
    assert whisper_calls[0][1] == "en"
    assert list(tmp_path.glob("*.vtt*")) == []
    assert sorted(path.suffix for path in tmp_path.iterdir()) == [".mp3"]
    assert len(SubtitleFailureThenMediaDownloader.calls) == 2
    subtitle_options, _ = SubtitleFailureThenMediaDownloader.calls[0]
    media_calls = [
        call for call in SubtitleFailureThenMediaDownloader.calls
        if not call[0].get("skip_download")
    ]
    assert subtitle_options["skip_download"] is True
    assert subtitle_options["retries"] == 0
    assert len(media_calls) == 1
    media_options, media_download = media_calls[0]
    assert media_download is True
    assert "writesubtitles" not in media_options
    assert "writeautomaticsub" not in media_options
    for options, _ in SubtitleFailureThenMediaDownloader.calls:
        assert "cookiefile" not in options
        assert "cookiesfrombrowser" not in options


def test_genuine_media_failure_surfaces_and_cleans_all_job_files(
    monkeypatch, tmp_path
):
    SubtitleSuccessThenMediaFailureDownloader.calls = []
    monkeypatch.setattr(extractor_module, "MEDIA", tmp_path)
    monkeypatch.setattr(
        extractor_module, "YoutubeDL", SubtitleSuccessThenMediaFailureDownloader
    )
    monkeypatch.setattr(
        extractor_module,
        "whisper_transcribe",
        lambda path, language: pytest.fail("Whisper should not hide media failure"),
    )

    with pytest.raises(DownloadError, match="Media download failed"):
        extractor_module.extract_media(
            "https://example.invalid/lesson", None, None
        )

    assert len(SubtitleSuccessThenMediaFailureDownloader.calls) == 2
    assert list(tmp_path.iterdir()) == []


def test_whisper_failure_removes_downloaded_media_and_partial_subtitles(
    monkeypatch, tmp_path
):
    SubtitleFailureThenMediaDownloader.calls = []
    monkeypatch.setattr(extractor_module, "MEDIA", tmp_path)
    monkeypatch.setattr(
        extractor_module, "YoutubeDL", SubtitleFailureThenMediaDownloader
    )
    monkeypatch.setattr(
        extractor_module,
        "whisper_transcribe",
        lambda path, language: (_ for _ in ()).throw(
            RuntimeError("Whisper failed")
        ),
    )

    with pytest.raises(RuntimeError, match="Whisper failed"):
        extractor_module.extract_media(
            "https://example.invalid/lesson", None, None
        )

    assert list(tmp_path.iterdir()) == []
