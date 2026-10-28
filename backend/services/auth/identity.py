from __future__ import annotations

import inspect
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Protocol, runtime_checkable

from fastapi import Depends, HTTPException, Request


class DeploymentMode(str, Enum):
    PERSONAL = "personal"
    CLOUD = "cloud"


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Identity established by a trusted server-side session verifier."""

    user_id: str
    session_id: str
    authentication_method: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.user_id):
            raise ValueError("Authenticated user id is invalid")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", self.session_id):
            raise ValueError("Authenticated session id is invalid")
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", self.authentication_method
        ):
            raise ValueError("Authentication method is invalid")


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    mode: DeploymentMode
    principal: AuthenticatedPrincipal | None = None

    @property
    def authenticated_user_id(self) -> str | None:
        return self.principal.user_id if self.principal else None


class InvalidAuthentication(RuntimeError):
    """A configured trusted verifier rejected the request."""


@runtime_checkable
class TrustedIdentityVerifier(Protocol):
    def __call__(
        self, request: Request
    ) -> AuthenticatedPrincipal | None: ...


def get_trusted_identity_verifier(request: Request) -> object | None:
    """Return only a verifier installed by trusted application bootstrap."""

    return getattr(request.app.state, "identity_verifier", None)


VerifierDependency = Annotated[
    object | None, Depends(get_trusted_identity_verifier)
]


def deployment_mode() -> DeploymentMode:
    value = os.getenv("LANGUAGE_AI_DEPLOYMENT_MODE", "personal").strip().lower()
    try:
        return DeploymentMode(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "deployment_mode_invalid",
                "message": "The server deployment mode is not configured safely.",
            },
        ) from exc


async def get_request_identity(
    request: Request, verifier: VerifierDependency
) -> RequestIdentity:
    """Resolve identity without accepting any client impersonation header.

    P8-02A deliberately ships no public Email/OAuth verifier. A future trusted
    verifier must be installed by server bootstrap as ``app.state.identity_verifier``.
    Tests inject synthetic identities with FastAPI dependency overrides.
    """

    mode = deployment_mode()
    if mode is DeploymentMode.PERSONAL:
        return RequestIdentity(mode=mode)

    if verifier is None or not callable(verifier):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cloud_identity_not_configured",
                "message": "Cloud authentication is not configured.",
            },
        )
    try:
        principal = verifier(request)
        if inspect.isawaitable(principal):
            principal = await principal
    except InvalidAuthentication as exc:
        raise HTTPException(
            status_code=401,
            detail={"code": "authentication_invalid", "message": "Authentication is invalid."},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "identity_verifier_unavailable",
                "message": "Cloud authentication could not be verified.",
            },
        ) from exc
    if not isinstance(principal, AuthenticatedPrincipal):
        raise HTTPException(
            status_code=401,
            detail={"code": "authentication_required", "message": "Authentication is required."},
        )
    return RequestIdentity(mode=mode, principal=principal)


IdentityDependency = Annotated[RequestIdentity, Depends(get_request_identity)]


def owner_user_id(identity: RequestIdentity, claimed_user_id: str) -> str:
    """Return the server-owned tenant id, preserving the legacy local contract."""

    if identity.mode is DeploymentMode.PERSONAL:
        return claimed_user_id
    authenticated = identity.authenticated_user_id
    if authenticated is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "authentication_required", "message": "Authentication is required."},
        )
    if claimed_user_id != authenticated:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "identity_mismatch",
                "message": "The requested identity does not match the authenticated account.",
            },
        )
    return authenticated


def require_personal_storage(identity: IdentityDependency) -> RequestIdentity:
    """Fail closed around legacy stores that have no durable owner field.

    Library/media ownership migration belongs to P8-02B. Allowing authenticated
    cloud callers through before that migration would still permit horizontal
    access by resource id.
    """

    if identity.mode is DeploymentMode.CLOUD:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cloud_tenant_storage_not_configured",
                "message": "This resource is unavailable until tenant-owned storage is configured.",
            },
        )
    return identity


PersonalStorageDependency = Annotated[
    RequestIdentity, Depends(require_personal_storage)
]


def require_provider_cost_controls(identity: IdentityDependency) -> RequestIdentity:
    """Keep credential-bearing/provider routes closed until P8-02B quotas exist."""

    if identity.mode is DeploymentMode.CLOUD:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cloud_provider_controls_not_configured",
                "message": "Cloud provider budget and rate controls are not configured.",
            },
        )
    return identity


ProviderAccessDependency = Annotated[
    RequestIdentity, Depends(require_provider_cost_controls)
]
