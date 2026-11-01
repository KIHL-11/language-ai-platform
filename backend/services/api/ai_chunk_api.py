from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request

from services.ai_chunks.service import (
    AiChunkOrganizerService,
    CloudAiChunkOrganizerService,
    AiChunkProviderError,
    AiChunkValidationError,
)
from services.ai_chunks.postgres import (
    CloudAiChunkOwnerNotProvisionedError,
    CloudAiChunkUnavailableError,
    PostgresOwnerScopedAiChunkRepository,
)
from services.ai_chunks.store import CandidateConflictError, CandidateNotFoundError
from services.auth.identity import DeploymentMode, IdentityDependency, owner_user_id
from services.schemas.ai_chunks import (
    AiChunkBatch,
    CandidateDecisionRequest,
    CandidateDecisionResponse,
    ChunkSuggestionRequest,
)
from services.library.store import LibraryStoreError
from services.study_bank.store import StudyBankStoreError
from services.study_bank.owner_repository import OwnerScopedStudyBankRepository
from services.library.owner_repository import OwnerScopedLibraryRepository
from services.retrieval.chunk_store import ChunkStoreError


router = APIRouter()
_service: AiChunkOrganizerService | None = None


def organizer() -> AiChunkOrganizerService:
    global _service
    if _service is None:
        from services.api import chunk_api, library_api, study_bank_api

        _service = AiChunkOrganizerService(
            library_api.library_store,
            study_bank_api.study_bank_store,
            chunk_memory=chunk_api.chunk_memory_service,
        )
    return _service


def _owner_and_service(http_request: Request, identity, claimed_user_id: str):
    owner = owner_user_id(identity, claimed_user_id)
    if identity.mode is not DeploymentMode.CLOUD:
        return owner, organizer()
    candidates = getattr(http_request.app.state, "cloud_ai_chunk_repository", None)
    study_bank = getattr(http_request.app.state, "cloud_study_bank_repository", None)
    library = getattr(http_request.app.state, "cloud_library_repository", None)
    if (
        not isinstance(candidates, PostgresOwnerScopedAiChunkRepository)
        or not isinstance(study_bank, OwnerScopedStudyBankRepository)
        or not isinstance(library, OwnerScopedLibraryRepository)
        or candidates.study_bank is not study_bank
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cloud_ai_candidate_storage_not_configured",
                "message": "Owner-scoped AI candidate and Study Bank storage is not configured.",
            },
        )
    return owner, CloudAiChunkOrganizerService(
        library,
        study_bank,
        candidates,
        clock=candidates.clock,
    )


def _controlled(operation):
    try:
        return operation()
    except CandidateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Chunk suggestion batch not found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chunk suggestion source not found") from exc
    except CandidateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (AiChunkValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AiChunkProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "ai_chunk_provider_invalid", "message": str(exc)},
        ) from exc
    except CloudAiChunkOwnerNotProvisionedError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cloud_owner_not_provisioned",
                "message": "The authenticated cloud account is not provisioned.",
            },
        ) from exc
    except CloudAiChunkUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cloud_ai_candidate_storage_unavailable",
                "message": "Cloud AI candidate storage is unavailable.",
            },
        ) from exc
    except (LibraryStoreError, StudyBankStoreError, ChunkStoreError) as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "ai_chunk_storage_unavailable", "message": "Chunk Organizer storage is unavailable."},
        ) from exc


@router.post("/ai/chunk-suggestions", response_model=AiChunkBatch)
def create_suggestions(
    request: ChunkSuggestionRequest,
    identity: IdentityDependency,
    http_request: Request,
):
    owner, service = _owner_and_service(http_request, identity, request.user_id)
    return _controlled(lambda: service.suggest(owner, request))


@router.get("/ai/chunk-suggestions/{batch_id}", response_model=AiChunkBatch)
def get_suggestions(
    batch_id: Annotated[str, Path(min_length=1, max_length=64)],
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    identity: IdentityDependency,
    http_request: Request,
):
    owner, service = _owner_and_service(http_request, identity, user_id)
    return _controlled(lambda: service.get_batch(owner, batch_id))


@router.post(
    "/ai/chunk-suggestions/{batch_id}/decisions",
    response_model=CandidateDecisionResponse,
)
def decide_suggestion(
    batch_id: Annotated[str, Path(min_length=1, max_length=64)],
    request: CandidateDecisionRequest,
    identity: IdentityDependency,
    http_request: Request,
):
    owner, service = _owner_and_service(http_request, identity, request.user_id)
    return _controlled(lambda: service.decide(owner, batch_id, request))
