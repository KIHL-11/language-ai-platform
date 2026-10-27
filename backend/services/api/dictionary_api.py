from fastapi import APIRouter, HTTPException

from services.dictionary.runtime_status import runtime_status
from services.dictionary.service import DictionaryService
from services.schemas.dictionary import (
    DictionaryLookupRequest,
    DictionaryLookupResponse,
    DictionaryRuntimeStatus,
)


router = APIRouter()
dictionary_service = DictionaryService.default()


@router.post("/dictionary/lookup", response_model=DictionaryLookupResponse)
def dictionary_lookup(request: DictionaryLookupRequest):
    try:
        return dictionary_service.lookup(
            request.language,
            request.word,
            request.sentence,
            include_inflections=request.include_inflections,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Local dictionary data could not be read") from exc


@router.get("/dictionary/status", response_model=DictionaryRuntimeStatus)
def dictionary_status():
    """Dictionary-aware health used by migration smoke checks; never returns paths."""
    provider = dictionary_service.provider
    return runtime_status(
        getattr(provider, "path", None),
        getattr(provider, "forms_index_path", None),
    )
