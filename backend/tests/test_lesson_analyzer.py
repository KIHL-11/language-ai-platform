import json

import requests

from services.ai.lesson_analyzer import analyze_sentences, parse_analysis_json
from services.ai.llm_client import LLMRateLimitError


class FakeLLM:
    provider = "openai"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def test_json_parser_accepts_plain_json_array():
    parsed = parse_analysis_json(
        '[{"id": 1, "translation": "你好", "keywords": [], "chunks": [], "grammar": ""}]'
    )

    assert parsed == [
        {
            "id": 1,
            "translation": "你好",
            "keywords": [],
            "chunks": [],
            "grammar": "",
        }
    ]


def test_json_parser_removes_markdown_code_fence():
    payload = [{"id": 7, "translation": "Guten Tag"}]
    parsed = parse_analysis_json(f"```json\n{json.dumps(payload)}\n```")

    assert parsed == payload


def test_json_parser_extracts_outer_json_from_surrounding_text():
    parsed = parse_analysis_json(
        'Here is the result: [{"id": 3, "translation": "drei"}] Thanks.'
    )

    assert parsed == [{"id": 3, "translation": "drei"}]


def test_mock_mode_without_injected_client_returns_mock_analysis(monkeypatch):
    monkeypatch.setenv("AI_MODE", "mock")
    monkeypatch.setattr(
        "services.ai.lesson_analyzer.llm.chat",
        lambda messages: (_ for _ in ()).throw(AssertionError("LLM must not be called")),
    )
    sentence = {"id": 1, "text": "Local lesson.", "start": 0.0, "end": 1.0}

    analyzed = analyze_sentences([sentence], "en")

    assert analyzed[0]["translation"] == "Mock translation: Local lesson."
    assert analyzed[0]["ai_status"] == "ok"


def test_injected_client_takes_precedence_over_mock_mode(monkeypatch):
    monkeypatch.setenv("AI_MODE", "mock")
    fake_llm = FakeLLM(
        [
            json.dumps(
                [
                    {
                        "id": 1,
                        "translation": "Injected translation",
                        "keywords": [],
                        "chunks": [],
                        "grammar": "",
                    }
                ]
            )
        ]
    )
    sentence = {"id": 1, "text": "Use injection.", "start": 0.0, "end": 1.0}

    analyzed = analyze_sentences([sentence], "en", llm_client=fake_llm)

    assert len(fake_llm.calls) == 1
    assert analyzed[0]["translation"] == "Injected translation"


def test_analyzer_batches_and_merges_only_analysis_fields_by_id():
    sentences = [
        {"id": index, "text": f"Source {index}", "start": index + 0.1, "end": index + 0.9}
        for index in range(1, 22)
    ]
    responses = []
    for ids in (range(1, 11), range(11, 21), range(21, 22)):
        responses.append(
            json.dumps(
                [
                    {
                        "id": sentence_id,
                        "text": "AI must not replace this",
                        "start": 999,
                        "end": 1000,
                        "translation": f"Translation {sentence_id}",
                        "keywords": [],
                        "chunks": [],
                        "grammar": "",
                    }
                    for sentence_id in reversed(list(ids))
                ]
            )
        )
    fake_llm = FakeLLM(responses)

    analyzed = analyze_sentences(
        sentences,
        source_language="en",
        target_language="zh-CN",
        batch_size=10,
        llm_client=fake_llm,
    )

    assert len(fake_llm.calls) == 3
    assert [sentence["id"] for sentence in analyzed] == list(range(1, 22))
    assert analyzed[0]["text"] == "Source 1"
    assert analyzed[0]["start"] == 1.1
    assert analyzed[0]["end"] == 1.9
    assert analyzed[0]["translation"] == "Translation 1"
    assert analyzed[0]["ai_status"] == "ok"


def test_malformed_response_falls_back_without_losing_sentence():
    sentence = {"id": 1, "text": "Keep me.", "start": 3.2, "end": 4.8}

    analyzed = analyze_sentences(
        [sentence], "en", llm_client=FakeLLM(["not valid JSON"])
    )

    assert analyzed == [
        {
            "id": 1,
            "text": "Keep me.",
            "translation": "",
            "start": 3.2,
            "end": 4.8,
            "keywords": [],
            "chunks": [],
            "grammar": "",
            "ai_status": "failed",
        }
    ]


def test_ai_429_falls_back_to_usable_sentence(monkeypatch):
    monkeypatch.setenv("AI_MODE", "mock")
    sentence = {"id": 1, "text": "Still usable.", "start": 0.0, "end": 1.0}
    analyzed = analyze_sentences(
        [sentence],
        "en",
        llm_client=FakeLLM([LLMRateLimitError("HTTP 429")]),
    )

    assert analyzed[0]["text"] == "Still usable."
    assert analyzed[0]["ai_status"] == "failed"


def test_ai_timeout_falls_back_to_usable_sentence(monkeypatch):
    monkeypatch.setenv("AI_MODE", "mock")
    sentence = {"id": 1, "text": "Noch nutzbar.", "start": 2.0, "end": 3.0}
    analyzed = analyze_sentences(
        [sentence],
        "de",
        llm_client=FakeLLM([requests.Timeout("timed out")]),
    )

    assert analyzed[0]["text"] == "Noch nutzbar."
    assert analyzed[0]["ai_status"] == "failed"


def test_partial_batch_failure_marks_only_failed_batch():
    sentences = [
        {"id": 1, "text": "One.", "start": 0.0, "end": 1.0},
        {"id": 2, "text": "Two.", "start": 1.0, "end": 2.0},
        {"id": 3, "text": "Three.", "start": 2.0, "end": 3.0},
    ]
    first_batch = json.dumps(
        [
            {"id": 1, "translation": "一", "keywords": [], "chunks": [], "grammar": ""},
            {"id": 2, "translation": "二", "keywords": [], "chunks": [], "grammar": ""},
        ]
    )

    analyzed = analyze_sentences(
        sentences,
        "en",
        batch_size=2,
        llm_client=FakeLLM([first_batch, requests.Timeout("timed out")]),
    )

    assert [sentence["ai_status"] for sentence in analyzed] == ["ok", "ok", "failed"]
    assert analyzed[2]["text"] == "Three."
