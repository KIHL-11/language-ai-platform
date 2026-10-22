import os
import subprocess

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from services.library.store import (
    LearningLibraryStore,
    LibraryConflictError,
    LibraryStoreError,
)
from services.schemas.library import (
    LibraryItem,
    LibraryListResponse,
    LibraryProgressRequest,
    LibrarySaveRequest,
    ListeningProgressRequest,
)
from services.schemas.shadowing import ShadowingAttemptResponse
from services.shadowing.analyzer import ShadowingAnalyzer
from services.shadowing.audio import prepared_upload_wav
from services.transcript.hygiene import clean_transcript_text


router = APIRouter()
library_store = LearningLibraryStore()
MAX_SHADOWING_UPLOAD_BYTES = 10 * 1024 * 1024
SAFE_AUDIO_SUFFIXES = {".webm", ".ogg", ".wav", ".mp3", ".m4a", ".mp4"}


def _store_error(operation):
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Library item not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LibraryStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/library/items", response_model=LibraryItem)
def save_library_item(request: LibrarySaveRequest):
    item = _store_error(
        lambda: library_store.save_item(request.lesson, request.progress)
    )
    return library_store.item_for_response(item)


@router.get("/library/items", response_model=LibraryListResponse)
def list_library_items():
    return LibraryListResponse(items=_store_error(library_store.list_summaries))


@router.get("/library/items/{item_id}", response_model=LibraryItem)
def get_library_item(item_id: str):
    item = _store_error(lambda: library_store.get_item(item_id))
    return library_store.item_for_response(item)


@router.patch("/library/items/{item_id}/progress", response_model=LibraryItem)
def update_library_progress(item_id: str, request: LibraryProgressRequest):
    item = _store_error(
        lambda: library_store.update_progress(item_id, request.model_dump())
    )
    return library_store.item_for_response(item)


@router.patch("/library/items/{item_id}/listening-progress", response_model=LibraryItem)
def update_library_listening_progress(
    item_id: str, request: ListeningProgressRequest
):
    item = _store_error(
        lambda: library_store.update_listening_progress(
            item_id, request.model_dump()
        )
    )
    return library_store.item_for_response(item)


@router.delete("/library/items/{item_id}", status_code=204)
def delete_library_item(item_id: str):
    _store_error(lambda: library_store.delete_item(item_id))
    return Response(status_code=204)


@router.get("/library/items/{item_id}/audio", response_class=FileResponse)
def get_library_audio(item_id: str):
    path, content_type = _store_error(lambda: library_store.audio_path(item_id))
    return FileResponse(path, media_type=content_type)


@router.post(
    "/library/items/{item_id}/shadowing/attempt",
    response_model=ShadowingAttemptResponse,
)
async def analyze_shadowing_attempt(
    item_id: str,
    audio: UploadFile = File(...),
    sentence_id: int = Form(...),
    expected_attempt_count: int = Form(...),
    client_recording_duration: float | None = Form(default=None),
):
    if expected_attempt_count < 0:
        raise HTTPException(status_code=422, detail="Invalid expected attempt count")
    if client_recording_duration is not None and (
        client_recording_duration < 0 or client_recording_duration > 30
    ):
        raise HTTPException(status_code=422, detail="Invalid recording duration")
    try:
        item = library_store.get_item(item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Library item not found") from exc
    sentence = next((value for value in item.lesson.sentences if value.id == sentence_id), None)
    if sentence is None:
        raise HTTPException(status_code=422, detail="Invalid shadowing sentence id")
    if not clean_transcript_text(sentence.text):
        raise HTTPException(status_code=422, detail="Sentence has no practiceable transcript text")
    current = item.shadowing_progress.sentences.get(str(sentence_id))
    if (current.attempt_count if current else 0) != expected_attempt_count:
        raise HTTPException(status_code=409, detail="Shadowing attempt state is stale")

    raw = await audio.read(MAX_SHADOWING_UPLOAD_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="Shadowing audio is empty")
    if len(raw) > MAX_SHADOWING_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Shadowing audio is too large")
    suffix = os.path.splitext(audio.filename or "")[1].lower()
    if suffix not in SAFE_AUDIO_SUFFIXES:
        suffix = ".webm"

    try:
        with prepared_upload_wav(raw, suffix) as wav:
            metrics = await run_in_threadpool(
                ShadowingAnalyzer(library_store).analyze,
                item_id,
                sentence_id,
                str(wav),
            )
        updated = library_store.record_shadowing_attempt(
            item_id,
            sentence_id,
            expected_attempt_count,
            metrics,
        )
    except LibraryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Library item or audio not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=400, detail="Shadowing audio could not be decoded") from exc
    except LibraryStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not update Learning Library") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Local shadowing analysis failed") from exc
    progress = updated.shadowing_progress.sentences[str(sentence_id)]
    return ShadowingAttemptResponse(
        sentence_id=sentence_id,
        progress=progress,
        metrics=metrics,
        sync_target_met=metrics.sync_target_met,
        stable_sync_achieved=progress.consecutive_target_hits >= 3,
    )
