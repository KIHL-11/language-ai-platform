from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response

from services.library.store import LibraryStoreError
from services.schemas.lesson import SourceLanguage
from services.retrieval.chunk_store import ChunkStoreError
from services.schemas.study_bank import (
    ContextualWordSaveRequest,
    ContextualWordSaveResult,
    ContextualWordStatusResult,
    MediaStudyProvenance,
    StudyBulkRequest,
    StudyBulkResult,
    StudyCandidateResponse,
    StudyItem,
    StudyItemPatch,
    StudyListResponse,
)
from services.study_bank.contextual_words import (
    ContextualDictionaryUnavailableError,
    ContextualWordError,
    ContextualWordService,
)
from services.study_bank.candidates import generate_candidates
from services.study_bank.store import (
    StudyBankStore,
    StudyBankStoreError,
    normalized_identity,
)


router = APIRouter()
study_bank_store = StudyBankStore()


def _contextual_word_service() -> ContextualWordService:
    from services.api import dictionary_api, library_api

    return ContextualWordService(
        library_api.library_store,
        study_bank_store,
        dictionary_api.dictionary_service,
    )


def _contextual_input(item_id: str, user_id: str, sentence_id: int, word: str):
    try:
        return _contextual_word_service().build_input(
            item_id, user_id, sentence_id, word
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Library item not found") from exc
    except ContextualWordError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ContextualDictionaryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LibraryStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not read Learning Library") from exc


def _store_error(operation):
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Study Bank item not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StudyBankStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _contextual_store_error(operation):
    """Keep M5.2 failures controlled without exposing local storage paths."""
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Study Bank item not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StudyBankStoreError as exc:
        raise HTTPException(
            status_code=500, detail="Could not read or update Study Bank"
        ) from exc


@router.get(
    "/library/items/{item_id}/study-candidates",
    response_model=StudyCandidateResponse,
)
def list_study_candidates(
    item_id: str,
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
):
    from services.api import library_api

    try:
        library_item = library_api.library_store.get_item(item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Library item not found") from exc
    except LibraryStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not read Learning Library") from exc
    saved = _store_error(lambda: study_bank_store.list_items(user_id, active=None))
    return StudyCandidateResponse(candidates=generate_candidates(library_item, saved))


@router.get(
    "/library/items/{item_id}/study-bank/words/status",
    response_model=ContextualWordStatusResult,
)
def contextual_word_status(
    item_id: str,
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    sentence_id: Annotated[int, Query(gt=0)],
    word: Annotated[str, Query(min_length=1, max_length=100)],
):
    value = _contextual_input(item_id, user_id, sentence_id, word)
    item = _contextual_store_error(lambda: _contextual_word_service().status(value))
    if item is None:
        return ContextualWordStatusResult(status="unsaved")
    return ContextualWordStatusResult(
        status="saved" if item.active else "archived",
        item=item,
    )


@router.post(
    "/library/items/{item_id}/study-bank/words",
    response_model=ContextualWordSaveResult,
)
def save_contextual_word(item_id: str, request: ContextualWordSaveRequest):
    value = _contextual_input(
        item_id, request.user_id, request.sentence_id, request.word
    )
    item, created = _contextual_store_error(lambda: _contextual_word_service().save(value))
    return ContextualWordSaveResult(item=item, created=created)


@router.get("/study-bank/items", response_model=StudyListResponse)
def list_study_items(
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    kind: Literal["word", "chunk"] | None = None,
    language: SourceLanguage | None = None,
    active: bool | None = True,
    library_item_id: str | None = None,
    search: Annotated[str | None, Query(max_length=500)] = None,
):
    items = _store_error(lambda: study_bank_store.list_items(
        user_id,
        kind=kind,
        language=language,
        active=active,
        library_item_id=library_item_id,
        search=search,
    ))
    return StudyListResponse(items=items)


@router.post("/study-bank/items/bulk", response_model=StudyBulkResult)
def save_study_items(request: StudyBulkRequest):
    from services.api import chunk_api

    _store_error(study_bank_store.assert_readable)
    for item in request.items:
        _store_error(lambda item=item: normalized_identity(item))
    for item in request.items:
        if item.kind != "chunk":
            continue
        source = item.provenance[0]
        if not isinstance(source, MediaStudyProvenance):
            raise HTTPException(status_code=422, detail="Curated media chunk requires media provenance")
        try:
            chunk, _ = chunk_api.chunk_memory_service.save_chunk(
                user_id=item.user_id,
                text=item.text,
                meaning=item.meaning,
                language=item.source_language,
                source_lesson_id=source.lesson_id,
                source_media_id=source.library_item_id,
                source_sentence_id=source.sentence_id,
                source_stage="study_bank",
            )
            if not chunk.active:
                chunk_api.chunk_memory_service.set_active(
                    item.user_id, chunk.id, True
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Curated chunk could not be synchronized to Chunk Memory",
            ) from exc
        except ChunkStoreError as exc:
            raise HTTPException(
                status_code=500,
                detail="Could not synchronize curated Chunk Memory",
            ) from exc
    items, created = _store_error(lambda: study_bank_store.save_items(request.items))
    return StudyBulkResult(items=items, created=created)


@router.patch("/study-bank/items/{item_id}", response_model=StudyItem)
def update_study_item(
    item_id: str,
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    request: StudyItemPatch,
):
    return _store_error(lambda: study_bank_store.update_item(user_id, item_id, request))


@router.delete("/study-bank/items/{item_id}", status_code=204)
def delete_study_item(
    item_id: str,
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
):
    _store_error(lambda: study_bank_store.delete_item(user_id, item_id))
    return Response(status_code=204)
