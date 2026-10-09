import json

import app as app_module
from fastapi.testclient import TestClient
from yt_dlp.utils import DownloadError

from services.ai.llm_client import LLMRateLimitError, llm
from services.api import lesson_api


client = TestClient(app_module.app)


def extracted_media():
    return {
        "title": "Short clip",
        "audio_url": "/media/short.mp3",
        "source": "subtitles",
        "vocals": None,
        "sentences": [
            {"id": 1, "text": "Hello world.", "start": 1.25, "end": 3.5}
        ],
    }


def ai_response():
    return json.dumps(
        [
            {
                "id": 1,
                "translation": "你好，世界。",
                "keywords": [],
                "chunks": [],
                "grammar": "",
            }
        ]
    )


def test_extract_route_keeps_legacy_request_compatible(monkeypatch):
    calls = []

    def fake_extract(url, start, end, vocals, source_language):
        calls.append((url, start, end, vocals, source_language))
        return extracted_media()

    monkeypatch.setattr(app_module, "extract_media", fake_extract)
    response = client.post(
        "/api/extract", json={"url": "https://youtu.be/short", "vocals": False}
    )

    assert response.status_code == 200
    assert response.json() == extracted_media()
    assert calls == [("https://youtu.be/short", None, None, False, "en")]


def test_lesson_create_route_still_accepts_direct_sentences(monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda messages: ai_response())

    response = client.post(
        "/api/lesson/create",
        json={"title": "Direct lesson", "sentences": ["Hello world."]},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Direct lesson"
    assert response.json()["sentences"][0]["text"] == "Hello world."
    assert response.json()["sentences"][0]["translation"] == "你好，世界。"


def test_lesson_from_url_builds_structured_lesson(monkeypatch):
    monkeypatch.setattr(
        lesson_api,
        "extract_media",
        lambda url, start, end, vocals, source_language: extracted_media(),
    )
    monkeypatch.setattr(llm, "chat", lambda messages: ai_response())

    response = client.post(
        "/api/lesson/from-url",
        json={
            "url": "https://youtu.be/short",
            "source_language": "en",
            "target_language": "zh-CN",
            "level": "B2",
            "start": None,
            "end": None,
            "vocals": False,
        },
    )

    assert response.status_code == 200
    lesson = response.json()
    assert lesson["source_url"] == "https://youtu.be/short"
    assert lesson["source_language"] == "en"
    assert lesson["sentences"][0]["start"] == 1.25
    assert lesson["sentences"][0]["end"] == 3.5
    assert lesson["sentences"][0]["translation"] == "你好，世界。"
    assert lesson["ai"]["status"] == "complete"


def test_lesson_from_url_rejects_unsupported_language():
    response = client.post(
        "/api/lesson/from-url",
        json={"url": "https://youtu.be/short", "source_language": "fr"},
    )

    assert response.status_code == 422


def test_lesson_from_url_rejects_invalid_time_range():
    response = client.post(
        "/api/lesson/from-url",
        json={
            "url": "https://youtu.be/short",
            "source_language": "de",
            "start": 5,
            "end": 5,
        },
    )

    assert response.status_code == 422


def test_lesson_from_url_returns_fallback_lesson_on_ai_429(monkeypatch):
    monkeypatch.setattr(
        lesson_api,
        "extract_media",
        lambda url, start, end, vocals, source_language: extracted_media(),
    )
    monkeypatch.setattr(
        llm, "chat", lambda messages: (_ for _ in ()).throw(LLMRateLimitError("HTTP 429"))
    )

    response = client.post(
        "/api/lesson/from-url",
        json={"url": "https://youtu.be/short", "source_language": "en"},
    )

    assert response.status_code == 200
    assert response.json()["sentences"][0]["text"] == "Hello world."
    assert response.json()["sentences"][0]["ai_status"] == "failed"
    assert response.json()["ai"]["status"] == "failed"


def test_lesson_from_url_accepts_whisper_fallback_transcript(monkeypatch):
    extracted = extracted_media()
    extracted["source"] = "whisper"
    extracted["sentences"][0]["text"] = "Fallback transcript."
    monkeypatch.setattr(
        lesson_api,
        "extract_media",
        lambda url, start, end, vocals, source_language: extracted,
    )
    monkeypatch.setattr(llm, "chat", lambda messages: ai_response())

    response = client.post(
        "/api/lesson/from-url",
        json={"url": "https://youtu.be/short", "source_language": "en"},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "whisper"
    assert response.json()["sentences"][0]["text"] == "Fallback transcript."


def test_lesson_from_url_returns_controlled_error_for_media_failure(monkeypatch):
    def fail_media(url, start, end, vocals, source_language):
        raise DownloadError("Media download failed")

    monkeypatch.setattr(lesson_api, "extract_media", fail_media)

    response = client.post(
        "/api/lesson/from-url",
        json={"url": "https://youtu.be/unavailable", "source_language": "en"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Media download failed"}
    assert "Traceback" not in response.text
