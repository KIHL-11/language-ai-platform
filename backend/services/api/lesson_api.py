from fastapi import APIRouter, HTTPException

from services.media.extractor import extract_media
from services.schemas.lesson import Lesson
from services.schemas.requests import LessonFromUrlRequest
from services.training.lesson import create_lesson


router = APIRouter()


@router.post("/lesson/create")
def generate_lesson(data: dict):

    title = data.get(
        "title",
        "Untitled Lesson"
    )

    sentences = data.get(
        "sentences",
        []
    )

    return create_lesson(
        title,
        sentences,
        source_url=data.get("source_url", ""),
        source_language=data.get("source_language", "en"),
        target_language=data.get("target_language", "zh-CN"),
        level=data.get("level", "B2"),
        audio_url=data.get("audio_url"),
        source=data.get("source", "subtitles"),
    )


@router.post("/lesson/from-url", response_model=Lesson)
def generate_lesson_from_url(request: LessonFromUrlRequest):
    try:
        extracted = extract_media(
            str(request.url),
            request.start,
            request.end,
            request.vocals,
            request.source_language,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not extracted.get("sentences") or extracted.get("source") not in {
        "subtitles",
        "whisper",
    }:
        raise HTTPException(
            status_code=400,
            detail="No usable subtitles or Whisper transcription were produced",
        )

    return create_lesson(
        extracted.get("title") or "Untitled Lesson",
        extracted["sentences"],
        source_url=str(request.url),
        source_language=request.source_language,
        target_language=request.target_language,
        level=request.level,
        audio_url=extracted.get("audio_url"),
        source=extracted["source"],
    )
