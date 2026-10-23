from fastapi import APIRouter, HTTPException

from services.dictionary.service import DictionaryService
from services.schemas.dictionary import DictionaryLookupRequest, DictionaryLookupResponse


router = APIRouter()
dictionary_service = DictionaryService.default()


@router.post("/dictionary/lookup", response_model=DictionaryLookupResponse)
def dictionary_lookup(request: DictionaryLookupRequest):
    try:
        return dictionary_service.lookup(request.language, request.word, request.sentence)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Local dictionary data could not be read") from exc
