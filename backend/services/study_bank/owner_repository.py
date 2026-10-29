from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol, runtime_checkable

from services.schemas.study_bank import (
    ReviewRating,
    StudyItem,
    StudyItemInput,
    StudyItemPatch,
)


@runtime_checkable
class OwnerScopedReviewRepository(Protocol):
    """Read/rate contract shared by the deterministic Daily Review service."""

    def list_items(
        self,
        owner_user_id: str,
        *,
        kind: str | None = None,
        language: str | None = None,
        active: bool | None = True,
        library_item_id: str | None = None,
        search: str | None = None,
    ) -> list[StudyItem]: ...

    def rate_item(
        self,
        owner_user_id: str,
        item_id: str,
        rating: ReviewRating,
        expected_review_count: int,
    ) -> tuple[StudyItem, datetime]: ...


@runtime_checkable
class OwnerScopedStudyBankRepository(OwnerScopedReviewRepository, Protocol):
    """Cloud Study Bank contract; every operation requires a trusted owner."""

    def get_item(self, owner_user_id: str, item_id: str) -> StudyItem: ...

    def save_items_for_owner(
        self, owner_user_id: str, values: Iterable[StudyItemInput]
    ) -> tuple[list[StudyItem], int]: ...

    def update_item(
        self,
        owner_user_id: str,
        item_id: str,
        patch: StudyItemPatch,
    ) -> StudyItem: ...

    def delete_item(
        self,
        owner_user_id: str,
        item_id: str,
        expected_revision: int | None = None,
    ) -> None: ...

    def assert_readable(self) -> None: ...

    def active_chunk_keys(self, owner_user_id: str, language: str) -> set[str]: ...
