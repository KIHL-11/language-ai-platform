import pytest
from pydantic import ValidationError

from services.schemas.lesson import Lesson
from services.schemas.requests import LessonFromUrlRequest


def test_english_lesson_schema_accepts_timestamped_sentences():
    lesson = Lesson.model_validate(
        {
            "id": "lesson-1",
            "title": "A short lesson",
            "source_url": "https://www.youtube.com/watch?v=example",
            "source_language": "en",
            "target_language": "zh-CN",
            "level": "B2",
            "audio_url": "/media/example.mp3",
            "source": "subtitles",
            "sentences": [
                {
                    "id": 1,
                    "text": "It is getting colder.",
                    "translation": "天气越来越冷了。",
                    "start": 1.25,
                    "end": 4.5,
                    "keywords": [],
                    "chunks": [],
                    "grammar": "",
                    "training": {
                        "blind_listening": True,
                        "dictation": True,
                        "shadowing": True,
                        "retell": True,
                    },
                    "ai_status": "ok",
                }
            ],
            "training_plan": {
                "active_recall": {"enabled": True, "count": 5},
                "blind_listening": {"enabled": True},
                "comprehension": {"enabled": True},
                "mini_dictation": {"enabled": True},
                "shadowing": {"enabled": True},
                "retell": {"enabled": True},
                "live_dialogue": {"enabled": True},
            },
            "training_order": [
                "active_recall",
                "blind_listening",
                "comprehension",
                "mini_dictation",
                "shadowing",
                "retell",
                "live_dialogue",
            ],
            "ai": {"provider": "openai", "status": "complete"},
        }
    )

    assert lesson.source_language == "en"
    assert lesson.sentences[0].start == 1.25
    assert lesson.sentences[0].end == 4.5


def test_german_lesson_schema_is_supported():
    lesson = Lesson.model_validate(
        {
            "id": "lesson-de",
            "title": "Deutsch im Alltag",
            "source_url": "https://youtu.be/example",
            "source_language": "de",
            "target_language": "zh-CN",
            "level": "B1",
            "audio_url": "/media/de.mp3",
            "source": "whisper",
            "sentences": [{"id": 1, "text": "Guten Morgen.", "start": 0, "end": 2}],
            "ai": {"provider": "openrouter", "status": "failed"},
        }
    )

    assert lesson.source_language == "de"
    assert lesson.sentences[0].text == "Guten Morgen."


@pytest.mark.parametrize("language", ["fr", "english", ""])
def test_lesson_from_url_rejects_unsupported_source_language(language):
    with pytest.raises(ValidationError):
        LessonFromUrlRequest(
            url="https://www.youtube.com/watch?v=example",
            source_language=language,
        )


def test_lesson_from_url_rejects_invalid_time_range():
    with pytest.raises(ValidationError, match="start must be less than end"):
        LessonFromUrlRequest(
            url="https://www.youtube.com/watch?v=example",
            source_language="en",
            start=12,
            end=12,
        )
