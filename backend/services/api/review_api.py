from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from services.api import study_bank_api
from services.review.service import (
    DEFAULT_NEW_LIMIT,
    DEFAULT_SESSION_LIMIT,
    MAX_SESSION_LIMIT,
    ReviewService,
)
from services.schemas.lesson import SourceLanguage
from services.schemas.review import (
    ReviewQueue,
    ReviewRatingRequest,
    ReviewRatingResponse,
    ReviewSummary,
)
from services.study_bank.store import StudyBankConflictError, StudyBankStoreError


router = APIRouter()


def _service() -> ReviewService:
    # Reuse the exact Study Bank singleton so rating and curation share one lock.
    return ReviewService(study_bank_api.study_bank_store)


@router.get("/review/summary", response_model=ReviewSummary)
def review_summary(
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    language: SourceLanguage | None = None,
):
    try:
        return _service().summary(user_id, language=language)
    except StudyBankStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not read Study Bank") from exc


@router.get("/review/queue", response_model=ReviewQueue)
def review_queue(
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    language: SourceLanguage | None = None,
    kind: Literal["word", "chunk"] | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_SESSION_LIMIT)] = DEFAULT_SESSION_LIMIT,
    new_limit: Annotated[int, Query(ge=0, le=MAX_SESSION_LIMIT)] = DEFAULT_NEW_LIMIT,
):
    try:
        return ReviewQueue(items=_service().queue(
            user_id,
            language=language,
            kind=kind,
            limit=limit,
            new_limit=new_limit,
        ))
    except StudyBankStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not read Study Bank") from exc


@router.post("/review/items/{item_id}/rate", response_model=ReviewRatingResponse)
def rate_review_item(item_id: str, request: ReviewRatingRequest):
    try:
        item, previous_due_at = _service().rate(
            request.user_id,
            item_id,
            request.rating,
            request.expected_review_count,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Study Bank item not found") from exc
    except StudyBankConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StudyBankStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not update Study Bank") from exc
    return ReviewRatingResponse(
        item=item,
        previous_due_at=previous_due_at,
        next_due_at=item.review.due_at,
        interval_seconds=item.review.interval_seconds,
    )
