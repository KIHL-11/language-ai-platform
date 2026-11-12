from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol, runtime_checkable

from services.schemas.lesson import Lesson
from services.schemas.library import LibraryItem, LibraryItemSummary


@dataclass(frozen=True, slots=True)
class OwnerScopedAudio:
    """Authorized object whose bytes matched the stored size and SHA-256.

    ``file`` is the handle that was hashed. Serve from it rather than reopening
    ``path``, which could have been swapped after verification. The caller owns
    the handle; use the value as a context manager when not streaming it.
    """

    path: Path
    content_type: str
    file: BinaryIO
    byte_size: int
    sha256: str

    def close(self) -> None:
        self.file.close()

    def __enter__(self) -> "OwnerScopedAudio":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


@runtime_checkable
class OwnerScopedLibraryRepository(Protocol):
    """Cloud Library contract whose every operation requires a trusted owner."""

    def list_for_owner(self, owner_user_id: str) -> list[LibraryItem]: ...

    def list_summaries_for_owner(
        self, owner_user_id: str
    ) -> list[LibraryItemSummary]: ...

    def get_for_owner(self, owner_user_id: str, item_id: str) -> LibraryItem: ...

    def create_for_owner(
        self,
        owner_user_id: str,
        lesson: Lesson,
        progress_value: Any | None = None,
    ) -> LibraryItem: ...

    def update_progress_for_owner(
        self, owner_user_id: str, item_id: str, progress_value: Any
    ) -> LibraryItem: ...

    def update_listening_progress_for_owner(
        self, owner_user_id: str, item_id: str, progress_value: Any
    ) -> LibraryItem: ...

    def delete_for_owner(self, owner_user_id: str, item_id: str) -> None: ...

    def audio_for_owner(
        self, owner_user_id: str, item_id: str
    ) -> OwnerScopedAudio: ...

    def item_for_response(self, item: LibraryItem) -> LibraryItem: ...
