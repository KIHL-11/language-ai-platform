from fastapi import APIRouter, HTTPException, Request

from services.auth.identity import DeploymentMode, IdentityDependency
from services.dictionary.cloud import cloud_dictionary_from_environment
from services.dictionary.service import DictionaryService
from services.schemas.dictionary import DictionaryLookupRequest, DictionaryLookupResponse


router = APIRouter()
dictionary_service = DictionaryService.default()


def _cloud_dictionary(http_request: Request, identity) -> DictionaryService:
    """Cloud lookups use only the configured cloud source, never the local
    Personal Desktop dictionary release; without one they fail closed."""

    if identity.authenticated_user_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "authentication_required", "message": "Authentication is required."},
        )
    service = getattr(http_request.app.state, "cloud_dictionary_service", None)
    if service is None:
        service = cloud_dictionary_from_environment()
        if service is not None:
            http_request.app.state.cloud_dictionary_service = service
    if not isinstance(service, DictionaryService) or service is dictionary_service:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cloud_dictionary_not_configured",
                "message": "The cloud dictionary is not configured.",
            },
        )
    return service


@router.post("/dictionary/lookup", response_model=DictionaryLookupResponse)
def dictionary_lookup(
    request: DictionaryLookupRequest,
    identity: IdentityDependency,
    http_request: Request,
):
    service = (
        _cloud_dictionary(http_request, identity)
        if identity.mode is DeploymentMode.CLOUD
        else dictionary_service
    )
    try:
        return service.lookup(
            request.language,
            request.word,
            request.sentence,
            include_inflections=request.include_inflections,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Local dictionary data could not be read") from exc
