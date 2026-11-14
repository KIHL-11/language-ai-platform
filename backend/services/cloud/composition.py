"""Cloud Phase 1 startup composition for the learning repositories.

Wires Library, Study Bank, AI Chunk and Topic PostgreSQL repositories into
``app.state`` - all of them or none. Nothing is wired unless the deployment is
CLOUD, the configuration is complete and valid, and the runtime connection
passes the database preflight (no SUPERUSER/BYPASSRLS/ownership, exact grants,
exact Phase 1 schema). On any failure the keys stay absent, so every Cloud
learning route keeps answering 503 through its existing access check.

Not wired here, on purpose: the B1 media cleanup worker (not on this branch,
never started), Cloud dictionary, Cloud speaking sessions, Realtime.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from services.cloud.preflight import CloudPreflightFailed, require_preflight
from services.cloud.schema import validate_version

LOGGER = logging.getLogger("language_ai.cloud.composition")

ENV_DSN = "CLOUD_STORAGE_DATABASE_URL"
ENV_MEDIA_ROOT = "CLOUD_MEDIA_ROOT"
ENV_MINIMUM_VERSION = "CLOUD_STORAGE_MINIMUM_VERSION"
DEFAULT_MINIMUM_VERSION = "0006"
RETRY_AFTER_SECONDS = 30.0

STATE_KEYS = (
    "cloud_library_repository",
    "cloud_study_bank_repository",
    "cloud_ai_chunk_repository",
    "cloud_topic_repository",
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN_MEDIA_PARENTS = (
    REPOSITORY_ROOT,  # covers backend/media, backend/storage and frontend/dist
)

_lock = threading.Lock()


class CloudStorageMisconfigured(ValueError):
    """The Cloud storage configuration is incomplete or unsafe."""


@dataclass(frozen=True, slots=True)
class CloudStorageSettings:
    dsn: str
    media_root: Path
    minimum_version: str = DEFAULT_MINIMUM_VERSION

    def __repr__(self) -> str:  # never expose the DSN
        return f"CloudStorageSettings(media_root={self.media_root!s}, minimum_version={self.minimum_version})"

    @classmethod
    def from_environment(cls, environ=os.environ) -> "CloudStorageSettings | None":
        dsn = environ.get(ENV_DSN, "").strip()
        media = environ.get(ENV_MEDIA_ROOT, "").strip()
        minimum = environ.get(ENV_MINIMUM_VERSION, "").strip() or DEFAULT_MINIMUM_VERSION
        if not dsn and not media:
            return None
        if not dsn or not media:
            raise CloudStorageMisconfigured(f"Both {ENV_DSN} and {ENV_MEDIA_ROOT} are required")
        try:
            validate_version(minimum)
        except ValueError as exc:
            raise CloudStorageMisconfigured(f"{ENV_MINIMUM_VERSION} is invalid") from exc
        return cls(dsn=dsn, media_root=validate_media_root(Path(media)), minimum_version=minimum)


def validate_media_root(path: Path) -> Path:
    if not path.is_absolute():
        raise CloudStorageMisconfigured(f"{ENV_MEDIA_ROOT} must be an absolute path")
    if path.is_symlink():
        raise CloudStorageMisconfigured(f"{ENV_MEDIA_ROOT} must not be a symbolic link")
    if not path.is_dir():
        raise CloudStorageMisconfigured(f"{ENV_MEDIA_ROOT} must be an existing directory")
    resolved = path.resolve()
    for parent in FORBIDDEN_MEDIA_PARENTS:
        if resolved == parent or parent in resolved.parents:
            raise CloudStorageMisconfigured(f"{ENV_MEDIA_ROOT} must be outside the application checkout")
    if not os.access(resolved, os.W_OK | os.X_OK):
        raise CloudStorageMisconfigured(f"{ENV_MEDIA_ROOT} is not writable")
    return resolved


@dataclass(frozen=True, slots=True)
class CloudRepositories:
    library: object
    study_bank: object
    ai_chunk: object
    topic: object

    def as_state(self) -> dict[str, object]:
        return dict(zip(STATE_KEYS, (self.library, self.study_bank, self.ai_chunk, self.topic)))


def build_cloud_repositories(
    settings: CloudStorageSettings,
    *,
    preflight: Callable[..., object] = require_preflight,
) -> CloudRepositories:
    """Preflight, then construct every repository. Raises on any failure."""
    from services.ai_chunks.postgres import PostgresOwnerScopedAiChunkRepository
    from services.library.postgres import FilesystemMediaObjectStore, PostgresOwnerScopedLibraryRepository
    from services.study_bank.postgres import PostgresOwnerScopedStudyBankRepository
    from services.topics.postgres import PostgresTopicRepository

    preflight(settings.dsn, minimum_version=settings.minimum_version)
    study_bank = PostgresOwnerScopedStudyBankRepository(settings.dsn)
    return CloudRepositories(
        library=PostgresOwnerScopedLibraryRepository(
            settings.dsn, FilesystemMediaObjectStore(settings.media_root)
        ),
        study_bank=study_bank,
        ai_chunk=PostgresOwnerScopedAiChunkRepository(settings.dsn, study_bank),
        topic=PostgresTopicRepository(settings.dsn),
    )


def configure_cloud_storage(app, repositories: CloudRepositories) -> bool:
    """Install all four repositories at once. Never overwrites anything already set."""
    if any(getattr(app.state, key, None) is not None for key in STATE_KEYS):
        return False
    for key, value in repositories.as_state().items():
        setattr(app.state, key, value)
    return True


def clear_cloud_storage(app) -> None:
    """Drop the references (shutdown/tests). Repositories hold no open connections."""
    for key in (*STATE_KEYS, "cloud_storage_status"):
        if hasattr(app.state, key):
            delattr(app.state, key)


@dataclass(frozen=True, slots=True)
class CloudStorageStatus:
    state: str  # "wired", "not_configured", "refused" (remembered) or "pre_injected"
    reasons: tuple[str, ...] = ()
    at: float = 0.0


def bootstrap_cloud_storage(app, *, environ=os.environ, clock: Callable[[], float] = time.monotonic,
                            build: Callable[..., CloudRepositories] = build_cloud_repositories) -> CloudStorageStatus:
    """Idempotent, thread-safe bootstrap called by the Cloud request boundary.

    The caller has already established that the deployment mode is CLOUD.
    A refusal is remembered and only retried after RETRY_AFTER_SECONDS, so a
    misconfigured server does not run preflight on every request.
    """
    def settled(current):
        return current is not None and (
            current.state in ("wired", "not_configured") or clock() - current.at < RETRY_AFTER_SECONDS
        )

    status = getattr(app.state, "cloud_storage_status", None)
    if settled(status):
        return status
    with _lock:
        status = getattr(app.state, "cloud_storage_status", None)
        if settled(status):
            return status
        if any(getattr(app.state, key, None) is not None for key in STATE_KEYS):
            # Installed by a trusted caller (tests); not remembered, never replaced.
            return CloudStorageStatus("pre_injected", at=clock())
        else:
            try:
                settings = CloudStorageSettings.from_environment(environ)
                if settings is None:
                    status = CloudStorageStatus("not_configured", at=clock())
                else:
                    repositories = build(settings)
                    configure_cloud_storage(app, repositories)
                    status = CloudStorageStatus("wired", at=clock())
            except CloudPreflightFailed as exc:
                status = CloudStorageStatus("refused", tuple(exc.failures), clock())
            except CloudStorageMisconfigured as exc:
                status = CloudStorageStatus("refused", (str(exc),), clock())
            except Exception as exc:  # constructor or driver failure: still all-or-nothing
                status = CloudStorageStatus("refused", (f"repository construction failed ({type(exc).__name__})",), clock())
            if status.state == "refused":
                LOGGER.warning("Cloud learning storage refused: %s", "; ".join(status.reasons))
        app.state.cloud_storage_status = status
        return status
