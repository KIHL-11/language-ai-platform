from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from services.auth.identity import DeploymentMode, IdentityDependency, RequestIdentity
from services.library.owner_repository import OwnerScopedLibraryRepository
from services.study_bank.owner_repository import OwnerScopedStudyBankRepository


@dataclass(frozen=True, slots=True)
class StudyBankAccess:
    identity: RequestIdentity
    owner_user_id: str | None = None
    repository: OwnerScopedStudyBankRepository | None = None
    library_repository: OwnerScopedLibraryRepository | None = None

    @property
    def is_cloud(self) -> bool:
        return self.identity.mode is DeploymentMode.CLOUD


def get_study_bank_access(
    request: Request, identity: IdentityDependency
) -> StudyBankAccess:
    if identity.mode is DeploymentMode.PERSONAL:
        return StudyBankAccess(identity=identity)
    owner = identity.authenticated_user_id
    if owner is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "authentication_required", "message": "Authentication is required."},
        )
    repository = getattr(request.app.state, "cloud_study_bank_repository", None)
    library = getattr(request.app.state, "cloud_library_repository", None)
    return StudyBankAccess(
        identity=identity,
        owner_user_id=owner,
        repository=(
            repository
            if isinstance(repository, OwnerScopedStudyBankRepository)
            else None
        ),
        library_repository=(
            library if isinstance(library, OwnerScopedLibraryRepository) else None
        ),
    )


StudyBankAccessDependency = Annotated[StudyBankAccess, Depends(get_study_bank_access)]
