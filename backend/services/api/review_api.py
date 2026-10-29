from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from services.auth.identity import owner_user_id
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
from services.study_bank.access import StudyBankAccessDependency
from services.study_bank.postgres import (
    CloudStudyBankUnavailableError,
    StudyBankOwnerNotProvisionedError,
)


router = APIRouter()


def _service(store=None) -> ReviewService:
    # Reuse the exact Study Bank singleton so rating and curation share one lock.
    return ReviewService(store or study_bank_api.study_bank_store)


def _review_error(exc: Exception):
    if isinstance(exc, StudyBankOwnerNotProvisionedError):
        return HTTPException(status_code=503, detail={"code": "cloud_owner_not_provisioned", "message": "The authenticated cloud account is not provisioned."})
    if isinstance(exc, CloudStudyBankUnavailableError):
        return HTTPException(status_code=503, detail={"code": "cloud_study_bank_unavailable", "message": "Cloud Study Bank storage is unavailable."})
    return HTTPException(status_code=500, detail="Could not access Study Bank")


def _store_for_access(access):
    if not access.is_cloud:
        return study_bank_api.study_bank_store
    if access.repository is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cloud_study_bank_storage_not_configured",
                "message": "Cloud Study Bank storage is not configured.",
            },
        )
    return access.repository


@router.get("/review/summary", response_model=ReviewSummary)
def review_summary(
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    access: StudyBankAccessDependency,
    language: SourceLanguage | None = None,
):
    user_id = owner_user_id(access.identity, user_id)
    store = _store_for_access(access)
    try:
        return _service(store).summary(user_id, language=language)
    except StudyBankStoreError as exc:
        raise _review_error(exc) from exc


@router.get("/review/queue", response_model=ReviewQueue)
def review_queue(
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    access: StudyBankAccessDependency,
    language: SourceLanguage | None = None,
    kind: Literal["word", "chunk"] | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_SESSION_LIMIT)] = DEFAULT_SESSION_LIMIT,
    new_limit: Annotated[int, Query(ge=0, le=MAX_SESSION_LIMIT)] = DEFAULT_NEW_LIMIT,
):
    user_id = owner_user_id(access.identity, user_id)
    store = _store_for_access(access)
    try:
        return ReviewQueue(items=_service(store).queue(
            user_id,
            language=language,
            kind=kind,
            limit=limit,
            new_limit=new_limit,
        ))
    except StudyBankStoreError as exc:
        raise _review_error(exc) from exc


@router.post("/review/items/{item_id}/rate", response_model=ReviewRatingResponse)
def rate_review_item(
    item_id: str, request: ReviewRatingRequest, access: StudyBankAccessDependency
):
    user_id = owner_user_id(access.identity, request.user_id)
    store = _store_for_access(access)
    try:
        item, previous_due_at = _service(store).rate(
            user_id,
            item_id,
            request.rating,
            request.expected_review_count,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Study Bank item not found") from exc
    except StudyBankConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StudyBankStoreError as exc:
        raise _review_error(exc) from exc
    return ReviewRatingResponse(
        item=item,
        previous_due_at=previous_due_at,
        next_due_at=item.review.due_at,
        interval_seconds=item.review.interval_seconds,
    )
