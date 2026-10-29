from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request

from services.auth.identity import DeploymentMode, IdentityDependency, owner_user_id, require_personal_storage
from services.schemas.lesson import LessonLevel, SourceLanguage
from services.schemas.topics import (
    TopicAttemptFinishRequest, TopicAttemptFinishResponse,
    TopicAttemptStartRequest, TopicAttemptState, TopicAttemptTurnRequest,
    TopicAttemptTurnResponse, TopicCatalogResponse, TopicPreviewRequest,
    TopicPreviewResponse,
)
from services.speaking.store import SpeakingConflictError, SpeakingStoreError
from services.study_bank.owner_repository import OwnerScopedStudyBankRepository
from services.topics.catalog import CATALOG_VERSION, get_topic, list_topics
from services.topics.service import (
    TopicCurriculumService, TopicStorageUnavailable, TopicTargetUnavailable,
)


router = APIRouter()


def _controlled(operation):
    try:
        return operation()
    except (KeyError, TopicTargetUnavailable) as exc:
        raise HTTPException(status_code=404, detail="Topic resource not found") from exc
    except SpeakingConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TopicStorageUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "topic_speaking_storage_unavailable",
                "message": str(exc),
            },
        ) from exc
    except SpeakingStoreError as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "topic_speaking_storage_error", "message": "Topic practice storage is unavailable."},
        ) from exc


@router.get("/topics", response_model=TopicCatalogResponse)
def topic_catalog(
    language: SourceLanguage | None = None,
    level: LessonLevel | None = None,
):
    return TopicCatalogResponse(catalog_version=CATALOG_VERSION, topics=list_topics(language, level))


@router.get("/topics/{topic_id}")
def topic_detail(topic_id: Annotated[str, Path(min_length=1, max_length=100)]):
    return _controlled(lambda: get_topic(topic_id))


def _service(http_request: Request, identity, *, attempt=False):
    from services.api import speaking_api

    if attempt:
        require_personal_storage(identity)
    if identity.mode is DeploymentMode.PERSONAL:
        return TopicCurriculumService(
            speaking_api.study_bank,
            speaking_api.speaking_store if attempt else None,
        )
    repository = getattr(http_request.app.state, "cloud_study_bank_repository", None)
    if not isinstance(repository, OwnerScopedStudyBankRepository):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cloud_study_bank_storage_not_configured",
                "message": "Owner-scoped Study Bank storage is not configured.",
            },
        )
    return TopicCurriculumService(repository, cloud=True)


@router.post("/topics/{topic_id}/preview", response_model=TopicPreviewResponse)
def preview_topic(
    topic_id: Annotated[str, Path(min_length=1, max_length=100)],
    body: TopicPreviewRequest,
    identity: IdentityDependency,
    http_request: Request,
):
    owner = owner_user_id(identity, body.user_id)
    service = _service(http_request, identity)
    return _controlled(lambda: service.preview(owner, topic_id, body.target_limit))


@router.post("/topics/{topic_id}/attempts", response_model=TopicAttemptState)
def start_topic_attempt(
    topic_id: Annotated[str, Path(min_length=1, max_length=100)],
    body: TopicAttemptStartRequest,
    identity: IdentityDependency,
    http_request: Request,
):
    owner = owner_user_id(identity, body.user_id)
    service = _service(http_request, identity, attempt=True)
    return _controlled(lambda: service.start(owner, topic_id, body.target_ids))


@router.post(
    "/topics/{topic_id}/attempts/{session_id}/turns",
    response_model=TopicAttemptTurnResponse,
)
def topic_attempt_turn(
    topic_id: Annotated[str, Path(min_length=1, max_length=100)],
    session_id: Annotated[str, Path(min_length=1, max_length=128)],
    body: TopicAttemptTurnRequest,
    identity: IdentityDependency,
    http_request: Request,
):
    owner = owner_user_id(identity, body.user_id)
    service = _service(http_request, identity, attempt=True)
    state, reply = _controlled(
        lambda: service.turn(owner, topic_id, session_id, body.expected_event_count, body.text)
    )
    return TopicAttemptTurnResponse(state=state, agent_reply=reply)


@router.post(
    "/topics/{topic_id}/attempts/{session_id}/finish",
    response_model=TopicAttemptFinishResponse,
)
def finish_topic_attempt(
    topic_id: Annotated[str, Path(min_length=1, max_length=100)],
    session_id: Annotated[str, Path(min_length=1, max_length=128)],
    body: TopicAttemptFinishRequest,
    identity: IdentityDependency,
    http_request: Request,
):
    owner = owner_user_id(identity, body.user_id)
    service = _service(http_request, identity, attempt=True)
    state, feedback = _controlled(
        lambda: service.finish(
            owner, topic_id, session_id, body.expected_event_count, body.interrupted
        )
    )
    return TopicAttemptFinishResponse(state=state, feedback=feedback)
