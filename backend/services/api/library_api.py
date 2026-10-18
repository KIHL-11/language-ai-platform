from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from services.library.store import LearningLibraryStore, LibraryStoreError
from services.schemas.library import (
    LibraryItem,
    LibraryListResponse,
    LibraryProgressRequest,
    LibrarySaveRequest,
    ListeningProgressRequest,
)


router = APIRouter()
library_store = LearningLibraryStore()


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
