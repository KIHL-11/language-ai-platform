from fastapi import APIRouter, HTTPException

from services.auth.identity import IdentityDependency

from services.media.discovery import (
    MediaDiscoveryService,
    VideoSearchProviderError,
    YtDlpVideoSearchProvider,
)
from services.media.subtitle_enrichment import YtDlpSubtitleEnrichmentProvider
from services.schemas.media_discovery import MediaCandidate, MediaDiscoveryRequest


router = APIRouter()
discovery_service = MediaDiscoveryService(
    YtDlpVideoSearchProvider(), YtDlpSubtitleEnrichmentProvider()
)


@router.post(
    "/media/discover",
    response_model=list[MediaCandidate],
)
def discover_media(
    request: MediaDiscoveryRequest, _identity: IdentityDependency
):
    try:
        return discovery_service.discover(request)
    except VideoSearchProviderError as exc:
        raise HTTPException(
            status_code=502, detail="Media discovery provider failed"
        ) from exc
