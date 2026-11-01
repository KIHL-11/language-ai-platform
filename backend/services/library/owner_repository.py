from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from services.schemas.lesson import Lesson
from services.schemas.library import LibraryItem, LibraryItemSummary


@dataclass(frozen=True, slots=True)
class OwnerScopedAudio:
    """Authorized local object returned after metadata ownership is verified."""

    path: Path
    content_type: str


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
