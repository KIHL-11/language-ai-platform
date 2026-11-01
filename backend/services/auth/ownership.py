"""Owner-scoped persistence contract for the P8-02B storage migration.

The current JSON Library does not implement this protocol. P8-02A therefore
keeps it inaccessible in cloud mode instead of pretending a client ``user_id``
is an ownership check.
"""

from __future__ import annotations

from typing import Protocol, TypeVar


ItemT = TypeVar("ItemT")
CreateT = TypeVar("CreateT")
UpdateT = TypeVar("UpdateT")


class OwnerScopedRepository(Protocol[ItemT, CreateT, UpdateT]):
    """Minimum contract required before a tenant resource may open in cloud.

    Implementations must scope every lookup and uniqueness check by the
    server-owned ``owner_user_id``. A missing resource and a resource owned by
    another tenant must be indistinguishable to callers.
    """

    def list_for_owner(self, owner_user_id: str) -> list[ItemT]: ...

    def get_for_owner(self, owner_user_id: str, resource_id: str) -> ItemT: ...

    def create_for_owner(self, owner_user_id: str, value: CreateT) -> ItemT: ...

    def update_for_owner(
        self, owner_user_id: str, resource_id: str, value: UpdateT
    ) -> ItemT: ...

    def delete_for_owner(self, owner_user_id: str, resource_id: str) -> None: ...
