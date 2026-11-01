from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from services.auth.identity import DeploymentMode, IdentityDependency, RequestIdentity
from services.library.owner_repository import OwnerScopedLibraryRepository


@dataclass(frozen=True, slots=True)
class LibraryAccess:
    identity: RequestIdentity
    owner_user_id: str | None = None
    cloud_repository: OwnerScopedLibraryRepository | None = None

    @property
    def is_cloud(self) -> bool:
        return self.identity.mode is DeploymentMode.CLOUD


def get_library_access(
    request: Request, identity: IdentityDependency
) -> LibraryAccess:
    if identity.mode is DeploymentMode.PERSONAL:
        return LibraryAccess(identity=identity)
    owner_user_id = identity.authenticated_user_id
    if owner_user_id is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "authentication_required",
                "message": "Authentication is required.",
            },
        )
    repository = getattr(request.app.state, "cloud_library_repository", None)
    if not isinstance(repository, OwnerScopedLibraryRepository):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cloud_tenant_storage_not_configured",
                "message": "Cloud tenant Library storage is not configured.",
            },
        )
    return LibraryAccess(
        identity=identity,
        owner_user_id=owner_user_id,
        cloud_repository=repository,
    )


LibraryAccessDependency = Annotated[LibraryAccess, Depends(get_library_access)]
