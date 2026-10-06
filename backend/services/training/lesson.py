import uuid

from services.ai.translator import translate_sentences
from services.ai.llm_client import llm
from services.schemas.lesson import Lesson


def _normalize_sentences(sentences: list[dict] | list[str]) -> list[dict]:
    normalized = []
    for index, sentence in enumerate(sentences, start=1):
        if isinstance(sentence, str):
            normalized.append(
                {"id": index, "text": sentence, "start": 0.0, "end": 0.0}
            )
            continue

        normalized.append(
            {
                "id": sentence.get("id", index),
                "text": sentence.get("text", ""),
                "start": sentence.get("start", 0.0),
                "end": sentence.get("end", 0.0),
            }
        )
    return normalized


def _lesson_ai_status(sentences: list[dict]) -> str:
    statuses = [sentence["ai_status"] for sentence in sentences]
    if statuses and all(status == "ok" for status in statuses):
        return "complete"
    if any(status == "ok" for status in statuses):
        return "partial"
    return "failed"


def create_lesson(
    title,
    sentences,
    *,
    source_url="",
    source_language="en",
    target_language="zh-CN",
    level="B2",
    audio_url=None,
    source="subtitles",
    llm_client=None,
):
    normalized = _normalize_sentences(sentences)
    analyzed = translate_sentences(
        normalized,
        target_language=target_language,
        source_language=source_language,
        llm_client=llm_client,
    )

    effective_client = llm_client if llm_client is not None else llm

    lesson = Lesson.model_validate(
        {
            "id": uuid.uuid4().hex,
            "title": title,
            "source_url": source_url,
            "source_language": source_language,
            "target_language": target_language,
            "level": level,
            "audio_url": audio_url,
            "source": source,
            "sentences": analyzed,
            "ai": {
                "provider": getattr(effective_client, "provider", "unknown"),
                "status": _lesson_ai_status(analyzed),
            },
        }
    )
    return lesson.model_dump()
