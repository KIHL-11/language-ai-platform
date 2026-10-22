from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from services.integration.recommendations import IntegrationService
from services.library.store import LibraryStoreError
from services.retrieval.chunk_store import ChunkStoreError
from services.schemas.integration import (
    IntegrationOverview,
    SpeakingSuggestionSaveRequest,
    SpeakingSuggestionSaveResponse,
    StudyPracticeEvidence,
    TargetLanguageList,
)
from services.schemas.lesson import SourceLanguage
from services.schemas.study_bank import SpeakingStudyProvenance, StudyItemInput
from services.speaking.store import SpeakingStoreError
from services.study_bank.store import StudyBankStoreError


router = APIRouter()


def _service() -> IntegrationService:
    # Import API-owned singletons lazily to avoid creating competing stores/locks.
    from services.api import chunk_api, library_api, speaking_api, study_bank_api

    return IntegrationService(
        study_bank_api.study_bank_store,
        library_api.library_store,
        speaking_api.speaking_store,
        chunk_api.chunk_memory_service,
    )


@router.get("/integration/overview", response_model=IntegrationOverview)
def integration_overview(
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    language: SourceLanguage | None = None,
):
    try:
        return _service().overview(user_id, language)
    except (StudyBankStoreError, LibraryStoreError, SpeakingStoreError, ChunkStoreError) as exc:
        raise HTTPException(status_code=500, detail="Could not build learning overview") from exc


@router.get(
    "/integration/study-items/{item_id}",
    response_model=StudyPracticeEvidence,
)
def study_item_evidence(
    item_id: str,
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
):
    try:
        return _service().evidence(user_id, item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Study Bank item not found") from exc
    except (StudyBankStoreError, LibraryStoreError, SpeakingStoreError, ChunkStoreError) as exc:
        raise HTTPException(status_code=500, detail="Could not build practice evidence") from exc


@router.get("/integration/target-languages", response_model=TargetLanguageList)
def target_languages(
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    source_language: SourceLanguage,
):
    try:
        return TargetLanguageList(
            target_languages=_service().target_languages(user_id, source_language)
        )
    except StudyBankStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not read target languages") from exc


@router.post(
    "/integration/speaking/sessions/{session_id}/suggestions/{suggestion_index}/save",
    response_model=SpeakingSuggestionSaveResponse,
)
def save_speaking_suggestion(
    session_id: str,
    suggestion_index: int,
    request: SpeakingSuggestionSaveRequest,
):
    from services.api import chunk_api, speaking_api, study_bank_api

    try:
        session = speaking_api.speaking_store.get(request.user_id, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Speaking session not found") from exc
    except SpeakingStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not read speaking session") from exc
    if session.status not in {"completed", "interrupted"}:
        raise HTTPException(status_code=409, detail="Speaking session review is not available")
    if suggestion_index < 0 or suggestion_index >= len(session.suggestions):
        raise HTTPException(status_code=404, detail="Speaking suggestion not found")

    suggestion = session.suggestions[suggestion_index]
    evidence = next(
        (
            turn.text
            for turn in session.turns
            if turn.sequence == suggestion.originating_turn
        ),
        None,
    )
    provenance = SpeakingStudyProvenance(
        speaking_session_id=session.id,
        suggestion_index=suggestion_index,
        originating_turn=suggestion.originating_turn,
        provider=session.provider,
        mode=session.mode,
        media_title_snapshot=session.media_title_snapshot,
        evidence_text=evidence[:2_000] if evidence else None,
    )
    value = StudyItemInput(
        user_id=request.user_id,
        kind=suggestion.kind,
        source_language=session.language,
        target_language=request.target_language,
        text=suggestion.text,
        meaning=request.meaning if request.meaning is not None else suggestion.meaning,
        usage=suggestion.short_usage_note or None,
        note=request.note,
        provenance=[provenance],
    )
    try:
        items, created = study_bank_api.study_bank_store.save_items([value])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StudyBankStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not save Speaking suggestion") from exc

    synchronized = False
    if suggestion.kind == "chunk":
        try:
            chunk, _ = chunk_api.chunk_memory_service.save_chunk(
                user_id=request.user_id,
                text=suggestion.text,
                meaning=value.meaning,
                language=session.language,
                source_lesson_id=None,
                source_media_id=None,
                source_sentence_id=None,
                source_stage="speaking",
            )
            if not chunk.active:
                chunk_api.chunk_memory_service.set_active(request.user_id, chunk.id, True)
            synchronized = True
        except (ValueError, ChunkStoreError) as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Speaking suggestion was saved to Study Bank, but Chunk Memory "
                    "synchronization failed"
                ),
            ) from exc

    return SpeakingSuggestionSaveResponse(
        item=items[0],
        created=bool(created),
        chunk_memory_synchronized=synchronized,
    )
