import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.api import library_api
from services.library.store import LearningLibraryStore, LibraryStoreError
from services.schemas.lesson import Lesson
from services.schemas.shadowing import ShadowingMetrics, ShadowingProgress, TimedWord
from services.shadowing.alignment import align_tokens, normalize_tokens
from services.shadowing.analyzer import ShadowingAnalyzer
from services.shadowing.metrics import calculate_metrics
from services.shadowing.reference import ReferenceTimingService
from services.transcription import local_whisper
from services.transcription.local_whisper import LocalTranscription


NOW = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
client = TestClient(app_module.app)


def lesson(language="en"):
    text = "I travel today to Berlin." if language == "en" else "Ich fahre heute nach Berlin."
    return Lesson.model_validate({
        "id": f"lesson-{language}", "title": "Shadow media",
        "source_url": "https://youtu.be/shadow1", "source_language": language,
        "target_language": "zh-CN", "level": "B2", "audio_url": None,
        "source": "subtitles", "sentences": [{
            "id": 1, "text": text, "translation": "今天去柏林。", "start": 1, "end": 4,
            "ai_status": "ok",
        }], "ai": {"provider": "mock", "status": "complete"},
    })


def store(tmp_path, *, times=None):
    clock = (lambda: next(times)) if times is not None else (lambda: NOW)
    return LearningLibraryStore(
        tmp_path / "library.json", tmp_path / "library_media", tmp_path / "media", clock,
    )


def words(values):
    return [TimedWord(word=word, start=start, end=end) for word, start, end in values]


REFERENCE = words([
    ("I", .10, .25), ("travel", .35, .75), ("today", .90, 1.20),
    ("to", 1.30, 1.42), ("Berlin", 1.55, 2.00),
])
ATTEMPT = words([
    ("I", .25, .40), ("travel", .52, .92), ("today", 1.08, 1.40),
    ("to", 1.48, 1.60), ("Berlin", 1.74, 2.18),
])


def metrics(hit=True, **updates):
    value = calculate_metrics(
        "I travel today to Berlin.",
        "I travel today to Berlin." if hit else "I travel Berlin.",
        ATTEMPT if hit else ATTEMPT[:2] + ATTEMPT[-1:],
        REFERENCE,
    ).model_dump()
    value.update(updates)
    return ShadowingMetrics.model_validate(value)


def test_normalization_supports_punctuation_apostrophes_and_german_unicode():
    assert normalize_tokens("  DON’T, stop! ") == ["don't", "stop"]
    assert normalize_tokens("STRASSE") == normalize_tokens("Straße")


@pytest.mark.parametrize(
    "recognized,counts",
    [
        ("one two three", (3, 0, 0, 0)),
        ("one three", (2, 1, 0, 0)),
        ("one small two three", (3, 0, 1, 0)),
        ("one four three", (2, 0, 0, 1)),
    ],
)
def test_alignment_classifies_edits_deterministically(recognized, counts):
    first = align_tokens(normalize_tokens("one two three"), normalize_tokens(recognized))
    second = align_tokens(normalize_tokens("one two three"), normalize_tokens(recognized))
    assert first == second
    assert tuple(sum(step.status == name for step in first) for name in ("matched", "missed", "extra", "substituted")) == counts


def test_transcript_match_uses_documented_edit_distance_formula():
    result = calculate_metrics("one two three four", "one too three", [], None)
    assert result.matched_count == 2
    assert result.missed_count == 1
    assert result.substituted_count == 1
    assert result.transcript_match_percent == 50
    assert result.timing_reference_available is False


def test_timing_metrics_measure_spans_start_rhythm_and_pauses():
    result = calculate_metrics("I travel today to Berlin", "I travel today to Berlin", ATTEMPT, REFERENCE)
    assert result.reference_speech_duration_ms == 1900
    assert result.attempt_speech_duration_ms == 1930
    assert result.duration_delta_ms == 30
    assert result.duration_delta_ratio == pytest.approx(30 / 1900, abs=0.0001)
    assert result.start_delay_ms == 150
    assert result.word_timing_mae_ms is not None
    assert result.pause_mae_ms is not None
    assert result.aligned_timed_word_count == 5
    assert result.sync_target_met is True


def test_reference_speech_span_uses_aligned_words_not_unmatched_asr_edges():
    reference = words([
        ("noise", 0, .1), ("hello", .3, .6), ("there", .8, 1.1),
        ("my", 1.2, 1.35), ("friend", 1.45, 1.8),
    ])
    attempt = words([
        ("hello", .4, .7), ("there", .9, 1.2),
        ("my", 1.3, 1.45), ("friend", 1.55, 1.9),
    ])
    result = calculate_metrics("hello there my friend", "hello there my friend", attempt, reference)
    assert result.timing_reference_available is True
    assert result.reference_speech_duration_ms == 1500
    assert result.attempt_speech_duration_ms == 1500


def test_negative_start_delay_and_short_sentence_optional_metrics():
    reference = words([("hello", .3, .6), ("there", .8, 1.1)])
    attempt = words([("hello", .1, .4), ("there", .62, .95)])
    result = calculate_metrics("hello there", "hello there", attempt, reference)
    assert result.start_delay_ms == -200
    assert result.word_timing_mae_ms is None
    assert result.pause_mae_ms is None
    assert result.sync_target_met is True


def test_unreliable_reference_and_no_speech_disable_timing_and_target():
    unreliable = calculate_metrics("one two three", "one two three", ATTEMPT[:3], words([("unrelated", 0, 1)]))
    silence = calculate_metrics("one two", "", [], REFERENCE[:2])
    assert unreliable.timing_reference_available is False
    assert unreliable.timing_unavailable_reason == "reference_alignment_unreliable"
    assert silence.recognized_word_count == 0
    assert silence.attempt_speech_duration_ms is None
    assert silence.sync_target_met is False


def test_library_v1_and_v2_read_without_write_then_mutate_to_v3(tmp_path):
    for version in (1, 2):
        value = store(tmp_path / str(version))
        item = value.save_item(lesson())
        payload = json.loads(value.path.read_text())
        payload["version"] = version
        payload["items"][0].pop("shadowing_progress", None)
        if version == 1:
            payload["items"][0].pop("listening_progress", None)
        original = json.dumps(payload)
        value.path.write_text(original, encoding="utf-8")
        restored = value.get_item(item.id)
        assert restored.shadowing_progress == ShadowingProgress()
        assert value.path.read_text(encoding="utf-8") == original
        value.update_progress(item.id, restored.progress.model_dump())
        assert json.loads(value.path.read_text())["version"] == 3


def test_library_v3_preserves_guided_listening_and_audio(tmp_path):
    value = store(tmp_path)
    value.source_media_root.mkdir(parents=True)
    (value.source_media_root / "audio.mp3").write_bytes(b"audio")
    source = lesson().model_copy(update={"audio_url": "/media/audio.mp3"})
    item = value.save_item(source)
    value.update_listening_progress(item.id, {"last_position_seconds": 2, "current_sentence_id": 1})
    updated = value.record_shadowing_attempt(item.id, 1, 0, metrics())
    assert updated.lesson == item.lesson
    assert updated.progress == item.progress
    assert updated.listening_progress.last_position_seconds == 2
    assert value.audio_path(item.id)[0].read_bytes() == b"audio"
    assert json.loads(value.path.read_text())["version"] == 3


def test_progress_counts_stability_best_values_and_miss_reset(tmp_path):
    times = iter([NOW, NOW, NOW, NOW, NOW])
    value = store(tmp_path, times=times)
    item = value.save_item(lesson())
    for expected in range(3):
        updated = value.record_shadowing_attempt(item.id, 1, expected, metrics())
    progress = updated.shadowing_progress.sentences["1"]
    assert progress.attempt_count == progress.target_hit_count == progress.consecutive_target_hits == 3
    assert progress.best_transcript_match_percent == 100
    assert progress.last_metrics is not None
    missed = value.record_shadowing_attempt(item.id, 1, 3, metrics(False))
    progress = missed.shadowing_progress.sentences["1"]
    assert progress.attempt_count == 4 and progress.target_hit_count == 3
    assert progress.consecutive_target_hits == 0
    assert missed.shadowing_progress.last_sentence_id == 1
    assert missed.shadowing_progress.last_practiced_at == NOW
    assert "history" not in missed.shadowing_progress.model_dump()


def test_stale_and_concurrent_attempts_cannot_double_count(tmp_path):
    value = store(tmp_path)
    item = value.save_item(lesson())
    barrier = threading.Barrier(2)

    def update():
        barrier.wait()
        try:
            value.record_shadowing_attempt(item.id, 1, 0, metrics())
            return "ok"
        except Exception as exc:
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: update(), range(2)))
    assert results.count("ok") == 1
    assert value.get_item(item.id).shadowing_progress.sentences["1"].attempt_count == 1


def test_malformed_and_future_library_are_not_overwritten(tmp_path):
    value = store(tmp_path)
    for payload in ("{broken", json.dumps({"version": 99, "items": []})):
        value.path.write_text(payload, encoding="utf-8")
        with pytest.raises(LibraryStoreError):
            value.save_item(lesson())
        assert value.path.read_text(encoding="utf-8") == payload


def test_summary_exposes_bounded_shadowing_activity(tmp_path):
    value = store(tmp_path)
    item = value.save_item(lesson())
    for expected in range(3):
        value.record_shadowing_attempt(item.id, 1, expected, metrics())
    summary = value.list_summaries()[0]
    assert summary.shadowing_attempt_count == 3
    assert summary.shadowing_target_sentence_count == 1
    assert summary.shadowing_stable_sentence_count == 1
    assert summary.shadowing_last_practiced_at == NOW


def test_audio_replacement_invalidates_reference_cache(tmp_path):
    value = store(tmp_path)
    value.source_media_root.mkdir(parents=True)
    source = value.source_media_root / "first.mp3"
    source.write_bytes(b"first")
    item = value.save_item(lesson().model_copy(update={"audio_url": "/media/first.mp3"}))
    cache = value._owned_media_directory(item.id) / "shadowing_reference_v1.json"
    cache.write_text("derived", encoding="utf-8")
    replacement = value.source_media_root / "second.mp3"
    replacement.write_bytes(b"second")
    value.save_item(lesson().model_copy(update={"audio_url": "/media/second.mp3"}))
    assert not cache.exists()
    assert value.audio_path(item.id)[0].read_bytes() == b"second"


def test_language_models_are_lazily_initialized_once_and_failures_can_retry(monkeypatch):
    class FakeModel:
        calls = 0

        def __init__(self, model_name, **settings):
            type(self).calls += 1
            assert model_name == "base" and settings["compute_type"] == "int8"

    monkeypatch.setenv("WHISPER_MODEL_MULTI", "base")
    monkeypatch.setattr("faster_whisper.WhisperModel", FakeModel)
    monkeypatch.setattr(local_whisper, "_models", {})
    with ThreadPoolExecutor(max_workers=4) as executor:
        models = list(executor.map(lambda _: local_whisper.get_local_whisper_model("de"), range(4)))
    assert len({id(model) for model in models}) == 1
    assert FakeModel.calls == 1

    class FlakyModel:
        calls = 0

        def __init__(self, *_args, **_settings):
            type(self).calls += 1
            if type(self).calls == 1:
                raise RuntimeError("initialization failed")

    monkeypatch.setattr("faster_whisper.WhisperModel", FlakyModel)
    monkeypatch.setattr(local_whisper, "_models", {})
    with pytest.raises(RuntimeError, match="initialization failed"):
        local_whisper.get_local_whisper_model("en")
    assert local_whisper.get_local_whisper_model("en") is not None
    assert FlakyModel.calls == 2


def test_reference_cache_reads_writes_regenerates_and_supports_languages(tmp_path, monkeypatch):
    value = store(tmp_path)
    value.source_media_root.mkdir(parents=True)
    (value.source_media_root / "audio.mp3").write_bytes(b"audio")
    item = value.save_item(lesson("de").model_copy(update={"audio_url": "/media/audio.mp3"}))
    calls = []

    @contextmanager
    def fake_segment(source, start, end):
        assert source.read_bytes() == b"audio" and (start, end) == (1, 4)
        yield tmp_path / "reference.wav"

    def transcriber(path, language):
        calls.append((path, language))
        return LocalTranscription("Ich fahre heute nach Berlin", words([
            ("Ich", 0, .2), ("fahre", .3, .6), ("heute", .7, 1), ("nach", 1.1, 1.3), ("Berlin", 1.4, 1.8),
        ]))

    monkeypatch.setattr("services.shadowing.reference.prepared_reference_segment", fake_segment)
    service = ReferenceTimingService(value, transcriber=transcriber)
    first, _ = service.get_words(item.id, 1)
    second, _ = service.get_words(item.id, 1)
    cache = value._owned_media_directory(item.id) / "shadowing_reference_v1.json"
    assert first == second and len(calls) == 1 and cache.is_file()
    cache.write_text("{broken", encoding="utf-8")
    service.get_words(item.id, 1)
    assert len(calls) == 2 and calls[0][1] == "de"


def test_reference_cache_generation_is_serialized(tmp_path, monkeypatch):
    value = store(tmp_path)
    value.source_media_root.mkdir(parents=True)
    (value.source_media_root / "audio.mp3").write_bytes(b"audio")
    item = value.save_item(lesson().model_copy(update={"audio_url": "/media/audio.mp3"}))
    calls = 0

    @contextmanager
    def fake_segment(*_):
        yield tmp_path / "reference.wav"

    def transcriber(*_):
        nonlocal calls
        calls += 1
        return LocalTranscription("I travel today to Berlin", REFERENCE)

    monkeypatch.setattr("services.shadowing.reference.prepared_reference_segment", fake_segment)
    service = ReferenceTimingService(value, transcriber=transcriber)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: service.get_words(item.id, 1), range(4)))
    assert all(result[0] == REFERENCE for result in results)
    assert calls == 1


def test_analyzer_uses_cleaned_target_without_rewriting_library(tmp_path, monkeypatch):
    value = store(tmp_path)
    noisy = lesson().model_copy(deep=True)
    noisy.sentences[0].text = "I [music] travel [APPLAUSE] today to Berlin."
    item = value.save_item(noisy)
    original = value.path.read_text(encoding="utf-8")
    captured = {}

    class FakeReferences:
        def get_words(self, item_id, sentence_id):
            assert (item_id, sentence_id) == (item.id, 1)
            return REFERENCE, None

    def fake_metrics(target, recognized, attempt, reference, **kwargs):
        captured["target"] = target
        return metrics()

    monkeypatch.setattr("services.shadowing.analyzer.calculate_metrics", fake_metrics)
    analyzer = ShadowingAnalyzer(
        value,
        transcriber=lambda *_: LocalTranscription("I travel today to Berlin", ATTEMPT),
        references=FakeReferences(),
    )
    analyzer.analyze(item.id, 1, str(tmp_path / "attempt.wav"))
    assert captured["target"] == "I travel today to Berlin."
    assert value.path.read_text(encoding="utf-8") == original


def test_noise_only_sentence_cannot_be_submitted_for_shadowing(tmp_path, monkeypatch):
    value = store(tmp_path)
    noisy = lesson().model_copy(deep=True)
    noisy.sentences[0].text = "[music] [APPLAUSE]"
    item = value.save_item(noisy)
    monkeypatch.setattr(library_api, "library_store", value)
    response = client.post(
        f"/api/library/items/{item.id}/shadowing/attempt",
        files={"audio": ("attempt.webm", b"recording", "audio/webm")},
        data={"sentence_id": "1", "expected_attempt_count": "0"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Sentence has no practiceable transcript text"


@pytest.fixture
def api_store(tmp_path, monkeypatch):
    value = store(tmp_path)
    item = value.save_item(lesson())
    monkeypatch.setattr(library_api, "library_store", value)

    @contextmanager
    def fake_wav(raw, suffix):
        assert raw == b"recording" and suffix in {".webm", ".m4a"}
        yield tmp_path / "attempt.wav"

    class FakeAnalyzer:
        def __init__(self, used_store):
            assert used_store is value

        def analyze(self, item_id, sentence_id, path):
            assert item_id == item.id and sentence_id == 1 and path.endswith("attempt.wav")
            return metrics()

    monkeypatch.setattr(library_api, "prepared_upload_wav", fake_wav)
    monkeypatch.setattr(library_api, "ShadowingAnalyzer", FakeAnalyzer)
    return value, item


def test_shadowing_attempt_api_updates_atomically_without_persisting_audio(api_store):
    value, item = api_store
    response = client.post(
        f"/api/library/items/{item.id}/shadowing/attempt",
        files={"audio": ("attempt.webm", b"recording", "audio/webm")},
        data={"sentence_id": "1", "expected_attempt_count": "0", "client_recording_duration": "2.3"},
    )
    assert response.status_code == 200
    assert response.json()["progress"]["attempt_count"] == 1
    assert response.json()["sync_target_met"] is True
    payload = value.path.read_text(encoding="utf-8")
    assert "recording" not in payload and "audio_b64" not in payload and "history" not in payload


def test_shadowing_attempt_api_accepts_browser_mp4_audio_suffix(api_store):
    _, item = api_store
    response = client.post(
        f"/api/library/items/{item.id}/shadowing/attempt",
        files={"audio": ("attempt.m4a", b"recording", "audio/mp4")},
        data={"sentence_id": "1", "expected_attempt_count": "0"},
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "files,data,status",
    [
        ({"audio": ("empty.webm", b"", "audio/webm")}, {"sentence_id": "1", "expected_attempt_count": "0"}, 400),
        ({"audio": ("attempt.webm", b"recording", "audio/webm")}, {"sentence_id": "99", "expected_attempt_count": "0"}, 422),
        ({"audio": ("attempt.webm", b"recording", "audio/webm")}, {"sentence_id": "1", "expected_attempt_count": "1"}, 409),
    ],
)
def test_shadowing_attempt_api_controls_invalid_inputs(api_store, files, data, status):
    _, item = api_store
    response = client.post(f"/api/library/items/{item.id}/shadowing/attempt", files=files, data=data)
    assert response.status_code == status


def test_shadowing_attempt_api_rejects_oversized_audio_before_transcoding(api_store, monkeypatch):
    _, item = api_store
    monkeypatch.setattr(library_api, "MAX_SHADOWING_UPLOAD_BYTES", 4)
    response = client.post(
        f"/api/library/items/{item.id}/shadowing/attempt",
        files={"audio": ("attempt.webm", b"12345", "audio/webm")},
        data={"sentence_id": "1", "expected_attempt_count": "0"},
    )
    assert response.status_code == 413
