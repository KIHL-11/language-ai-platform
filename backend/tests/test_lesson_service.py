import json

from services.ai.llm_client import LLMRateLimitError
from services.schemas.lesson import TRAINING_ORDER
from services.training.lesson import create_lesson


class FakeLLM:
    def __init__(self, provider, responses):
        self.provider = provider
        self.responses = iter(responses)

    def chat(self, messages):
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def analysis(sentence_id, translation):
    return {
        "id": sentence_id,
        "translation": translation,
        "keywords": [],
        "chunks": [],
        "grammar": "",
    }


def test_create_english_lesson_with_complete_ai_analysis():
    lesson = create_lesson(
        "Weather",
        [{"id": 1, "text": "It is cold.", "start": 1.5, "end": 3.25}],
        source_url="https://youtu.be/weather",
        source_language="en",
        audio_url="/media/weather.mp3",
        source="subtitles",
        llm_client=FakeLLM("openai", [json.dumps([analysis(1, "天气很冷。")])]),
    )

    assert lesson["source_language"] == "en"
    assert lesson["sentences"][0]["translation"] == "天气很冷。"
    assert lesson["sentences"][0]["start"] == 1.5
    assert lesson["sentences"][0]["end"] == 3.25
    assert lesson["ai"] == {"provider": "openai", "status": "complete"}
    assert lesson["training_order"] == TRAINING_ORDER


def test_create_german_lesson_survives_ai_failure():
    lesson = create_lesson(
        "Deutsch",
        [{"id": 1, "text": "Guten Morgen.", "start": 0.0, "end": 2.0}],
        source_url="https://youtu.be/deutsch",
        source_language="de",
        level="B1",
        source="whisper",
        llm_client=FakeLLM("openrouter", [LLMRateLimitError("HTTP 429")]),
    )

    assert lesson["source_language"] == "de"
    assert lesson["sentences"][0]["text"] == "Guten Morgen."
    assert lesson["sentences"][0]["translation"] == ""
    assert lesson["sentences"][0]["ai_status"] == "failed"
    assert lesson["ai"] == {"provider": "openrouter", "status": "failed"}


def test_create_lesson_reports_partial_batch_analysis(monkeypatch):
    monkeypatch.setenv("LESSON_AI_BATCH_SIZE", "1")
    lesson = create_lesson(
        "Partial",
        [
            {"id": 1, "text": "One.", "start": 0.0, "end": 1.0},
            {"id": 2, "text": "Two.", "start": 1.0, "end": 2.0},
        ],
        source="subtitles",
        llm_client=FakeLLM(
            "openai",
            [json.dumps([analysis(1, "一")]), LLMRateLimitError("HTTP 429")],
        ),
    )

    assert lesson["ai"]["status"] == "partial"
    assert [item["ai_status"] for item in lesson["sentences"]] == ["ok", "failed"]
