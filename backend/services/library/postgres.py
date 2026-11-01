from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from services.library.owner_repository import OwnerScopedAudio
from services.library.store import (
    LibraryStoreError,
    default_progress,
    source_key_for_lesson,
    summary_for_item,
    utc_now,
    validated_listening_progress,
    validated_progress,
)
from services.schemas.lesson import Lesson
from services.schemas.library import (
    LibraryItem,
    LibraryItemSummary,
    ListeningProgress,
    PersistentAudio,
)
from services.schemas.shadowing import ShadowingProgress


SAFE_OWNER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_ITEM_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
MAX_AUDIO_BYTES = 100 * 1024 * 1024


class OwnerNotProvisionedError(LibraryStoreError):
    """The trusted principal has no corresponding server-owned user row."""


class CloudLibraryUnavailableError(LibraryStoreError):
    """Cloud persistence failed without exposing driver or connection details."""


class FilesystemMediaObjectStore:
    """Test/development object adapter; never exposed as a static directory.

    Production object-storage selection and signed delivery remain deferred.  The
    adapter exists so tenant metadata and byte authorization can be exercised
    end-to-end without treating the Personal Desktop media directory as cloud
    storage.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._root = self.root.resolve()

    def put_audio(
        self, owner_user_id: str, item_id: str, filename: str, content: bytes
    ) -> str:
        if not SAFE_OWNER_ID.fullmatch(owner_user_id):
            raise LibraryStoreError("Invalid media owner")
        if not SAFE_ITEM_ID.fullmatch(item_id):
            raise LibraryStoreError("Invalid Library item id")
        if not content or len(content) > MAX_AUDIO_BYTES:
            raise LibraryStoreError("Invalid Library audio size")
        suffix = Path(filename).suffix.lower()
        if not suffix or len(suffix) > 10 or not re.fullmatch(r"\.[a-z0-9]+", suffix):
            suffix = ".bin"
        owner_key = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
        object_key = (
            f"owners/{owner_key}/library/{item_id}/"
            f"audio-{uuid.uuid4().hex}{suffix}"
        )
        destination = self._safe_path(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            self._fsync_directory(destination.parent)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise LibraryStoreError("Could not persist cloud Library audio") from exc
        return object_key

    def path_for(
        self, object_key: str, owner_user_id: str, item_id: str
    ) -> Path:
        self._validate_namespace(object_key, owner_user_id, item_id)
        candidate = self._safe_path(object_key)
        if not candidate.is_file():
            raise KeyError(object_key)
        return candidate

    def delete(self, object_key: str, owner_user_id: str, item_id: str) -> None:
        self._validate_namespace(object_key, owner_user_id, item_id)
        candidate = self._safe_path(object_key)
        try:
            candidate.unlink(missing_ok=True)
        except OSError as exc:
            raise LibraryStoreError("Could not remove cloud Library audio") from exc

    def _safe_path(self, object_key: str) -> Path:
        if (
            not object_key
            or object_key.startswith(('/', '\\'))
            or "\\" in object_key
            or any(part in {"", ".", ".."} for part in object_key.split("/"))
        ):
            raise LibraryStoreError("Unsafe media object key")
        candidate = (self._root / object_key).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise LibraryStoreError("Unsafe media object key") from exc
        return candidate

    @staticmethod
    def _validate_namespace(
        object_key: str, owner_user_id: str, item_id: str
    ) -> None:
        if not SAFE_OWNER_ID.fullmatch(owner_user_id) or not SAFE_ITEM_ID.fullmatch(
            item_id
        ):
            raise LibraryStoreError("Invalid media ownership namespace")
        owner_key = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
        pattern = re.compile(
            rf"^owners/{owner_key}/library/{re.escape(item_id)}/"
            r"audio-[0-9a-f]{32}\.[a-z0-9]+$"
        )
        if not pattern.fullmatch(object_key):
            raise LibraryStoreError("Media object ownership does not match metadata")

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        try:
            descriptor = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class PostgresOwnerScopedLibraryRepository:
    """PostgreSQL Library repository with owner predicates in every query."""

    def __init__(
        self,
        dsn: str,
        media_objects: FilesystemMediaObjectStore,
        clock: Callable[[], datetime] = utc_now,
    ):
        if not dsn:
            raise ValueError("A PostgreSQL DSN is required")
        self._dsn = dsn
        self.media_objects = media_objects
        self.clock = clock

    def list_for_owner(self, owner_user_id: str) -> list[LibraryItem]:
        self._validate_owner(owner_user_id)
        try:
            with self._connect() as connection:
                self._assert_owner_provisioned(connection, owner_user_id)
                rows = connection.execute(
                    self._item_select(
                        "WHERE item.owner_user_id = %s ORDER BY item.updated_at DESC"
                    ),
                    (owner_user_id,),
                ).fetchall()
                return [self._row_to_item(row) for row in rows]
        except psycopg.Error as exc:
            raise CloudLibraryUnavailableError("Cloud Library is unavailable") from exc

    def list_summaries_for_owner(
        self, owner_user_id: str
    ) -> list[LibraryItemSummary]:
        return [summary_for_item(item) for item in self.list_for_owner(owner_user_id)]

    def get_for_owner(self, owner_user_id: str, item_id: str) -> LibraryItem:
        self._validate_owner(owner_user_id)
        self._validate_item_id(item_id)
        try:
            with self._connect() as connection:
                self._assert_owner_provisioned(connection, owner_user_id)
                row = connection.execute(
                    self._item_select(
                        "WHERE item.owner_user_id = %s AND item.item_id = %s"
                    ),
                    (owner_user_id, item_id),
                ).fetchone()
                if row is None:
                    raise KeyError(item_id)
                return self._row_to_item(row)
        except KeyError:
            raise
        except psycopg.Error as exc:
            raise CloudLibraryUnavailableError("Cloud Library is unavailable") from exc

    def create_for_owner(
        self,
        owner_user_id: str,
        lesson: Lesson,
        progress_value: Any | None = None,
    ) -> LibraryItem:
        self._validate_owner(owner_user_id)
        now = self._now()
        # A /media path is a Personal Desktop artifact with no cloud ownership.
        cloud_lesson = lesson.model_copy(update={"audio_url": None})
        progress = (
            validated_progress(progress_value, cloud_lesson)
            if progress_value is not None
            else default_progress(cloud_lesson)
        )
        item_id = str(uuid.uuid4())
        try:
            with self._connect() as connection:
                self._assert_owner_provisioned(connection, owner_user_id)
                row = connection.execute(
                    """
                    INSERT INTO cloud_library_items (
                        owner_user_id, item_id, source_key, created_at, updated_at,
                        lesson_json, progress_json, listening_progress_json,
                        shadowing_progress_json, revision
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                    ON CONFLICT (owner_user_id, source_key) DO UPDATE SET
                        updated_at = EXCLUDED.updated_at,
                        lesson_json = EXCLUDED.lesson_json,
                        progress_json = EXCLUDED.progress_json,
                        revision = cloud_library_items.revision + 1
                    RETURNING item_id
                    """,
                    (
                        owner_user_id,
                        item_id,
                        source_key_for_lesson(cloud_lesson),
                        now,
                        now,
                        Jsonb(cloud_lesson.model_dump(mode="json")),
                        Jsonb(progress.model_dump(mode="json")),
                        Jsonb(ListeningProgress().model_dump(mode="json")),
                        Jsonb(ShadowingProgress().model_dump(mode="json")),
                    ),
                ).fetchone()
                stored_id = str(row["item_id"])
                result = connection.execute(
                    self._item_select(
                        "WHERE item.owner_user_id = %s AND item.item_id = %s"
                    ),
                    (owner_user_id, stored_id),
                ).fetchone()
        except psycopg.errors.ForeignKeyViolation as exc:
            raise OwnerNotProvisionedError("Cloud Library owner is not provisioned") from exc
        except psycopg.Error as exc:
            raise CloudLibraryUnavailableError("Cloud Library is unavailable") from exc
        if result is None:
            raise LibraryStoreError("Cloud Library write could not be read back")
        return self._row_to_item(result)

    def update_progress_for_owner(
        self, owner_user_id: str, item_id: str, progress_value: Any
    ) -> LibraryItem:
        return self._update_progress(owner_user_id, item_id, progress_value, listening=False)

    def update_listening_progress_for_owner(
        self, owner_user_id: str, item_id: str, progress_value: Any
    ) -> LibraryItem:
        return self._update_progress(owner_user_id, item_id, progress_value, listening=True)

    def delete_for_owner(self, owner_user_id: str, item_id: str) -> None:
        self._validate_owner(owner_user_id)
        self._validate_item_id(item_id)
        object_keys: list[str] = []
        cleanup_ids: dict[str, str] = {}
        try:
            with self._connect() as connection:
                self._assert_owner_provisioned(connection, owner_user_id)
                object_keys = [
                    row["object_key"]
                    for row in connection.execute(
                        """
                        SELECT object_key FROM cloud_library_media_assets
                        WHERE owner_user_id = %s AND library_item_id = %s
                        FOR UPDATE
                        """,
                        (owner_user_id, item_id),
                    ).fetchall()
                ]
                for object_key in object_keys:
                    cleanup_id = str(uuid.uuid4())
                    cleanup = connection.execute(
                        """
                        INSERT INTO cloud_media_cleanup_outbox (
                            cleanup_id, owner_user_id, library_item_id,
                            object_key, status, attempt_count, created_at,
                            updated_at
                        ) VALUES (%s, %s, %s, %s, 'pending', 0, %s, %s)
                        ON CONFLICT (object_key) DO UPDATE SET
                            updated_at = EXCLUDED.updated_at
                        RETURNING cleanup_id
                        """,
                        (
                            cleanup_id,
                            owner_user_id,
                            item_id,
                            object_key,
                            self._now(),
                            self._now(),
                        ),
                    ).fetchone()
                    cleanup_ids[object_key] = str(cleanup["cleanup_id"])
                deleted = connection.execute(
                    """
                    DELETE FROM cloud_library_items
                    WHERE owner_user_id = %s AND item_id = %s
                    RETURNING item_id
                    """,
                    (owner_user_id, item_id),
                ).fetchone()
                if deleted is None:
                    raise KeyError(item_id)
        except KeyError:
            raise
        except psycopg.Error as exc:
            raise CloudLibraryUnavailableError("Cloud Library is unavailable") from exc
        # Metadata is already inaccessible. The transaction retained a durable
        # cleanup record before deleting it, so object-store failures are
        # recoverable without keeping the Library item visible.
        for object_key in object_keys:
            try:
                self.media_objects.delete(object_key, owner_user_id, item_id)
            except LibraryStoreError:
                self._record_cleanup_failure(cleanup_ids[object_key])
            else:
                self._complete_cleanup(cleanup_ids[object_key])

    def audio_for_owner(self, owner_user_id: str, item_id: str) -> OwnerScopedAudio:
        self._validate_owner(owner_user_id)
        self._validate_item_id(item_id)
        try:
            with self._connect() as connection:
                self._assert_owner_provisioned(connection, owner_user_id)
                row = connection.execute(
                    """
                    SELECT media.object_key, media.content_type, media.byte_size
                    FROM cloud_library_media_assets AS media
                    JOIN cloud_library_items AS item
                      ON item.owner_user_id = media.owner_user_id
                     AND item.item_id = media.library_item_id
                    WHERE media.owner_user_id = %s
                      AND media.library_item_id = %s
                      AND media.asset_kind = 'audio'
                      AND media.status = 'ready'
                    """,
                    (owner_user_id, item_id),
                ).fetchone()
        except psycopg.Error as exc:
            raise CloudLibraryUnavailableError("Cloud Library is unavailable") from exc
        if row is None:
            raise KeyError(item_id)
        path = self.media_objects.path_for(
            row["object_key"], owner_user_id, item_id
        )
        if path.stat().st_size != row["byte_size"]:
            raise LibraryStoreError("Cloud Library audio integrity check failed")
        return OwnerScopedAudio(path=path, content_type=row["content_type"])

    def put_audio_for_owner(
        self,
        owner_user_id: str,
        item_id: str,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> LibraryItem:
        """Attach bytes already accepted by a trusted, future upload pipeline.

        This is intentionally not exposed as a public P8-02B1 upload endpoint.
        Tests use it to exercise real metadata/object ownership end to end.
        """

        self.get_for_owner(owner_user_id, item_id)
        object_key = self.media_objects.put_audio(
            owner_user_id, item_id, filename, content
        )
        digest = hashlib.sha256(content).hexdigest()
        media_id = str(uuid.uuid4())
        previous_key: str | None = None
        try:
            with self._connect() as connection:
                previous = connection.execute(
                    """
                    SELECT object_key FROM cloud_library_media_assets
                    WHERE owner_user_id = %s AND library_item_id = %s
                      AND asset_kind = 'audio'
                    FOR UPDATE
                    """,
                    (owner_user_id, item_id),
                ).fetchone()
                previous_key = previous["object_key"] if previous else None
                connection.execute(
                    """
                    INSERT INTO cloud_library_media_assets (
                        owner_user_id, media_id, library_item_id, asset_kind,
                        object_key, filename, content_type, byte_size, sha256,
                        status, created_at, updated_at
                    ) VALUES (%s, %s, %s, 'audio', %s, %s, %s, %s, %s,
                              'ready', %s, %s)
                    ON CONFLICT (owner_user_id, library_item_id, asset_kind)
                    DO UPDATE SET
                        object_key = EXCLUDED.object_key,
                        filename = EXCLUDED.filename,
                        content_type = EXCLUDED.content_type,
                        byte_size = EXCLUDED.byte_size,
                        sha256 = EXCLUDED.sha256,
                        status = EXCLUDED.status,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        owner_user_id,
                        media_id,
                        item_id,
                        object_key,
                        Path(filename).name[:255] or "audio.bin",
                        content_type
                        or mimetypes.guess_type(filename)[0]
                        or "application/octet-stream",
                        len(content),
                        digest,
                        self._now(),
                        self._now(),
                    ),
                )
        except Exception:
            self.media_objects.delete(object_key, owner_user_id, item_id)
            raise
        if previous_key and previous_key != object_key:
            try:
                self.media_objects.delete(previous_key, owner_user_id, item_id)
            except LibraryStoreError:
                pass
        return self.get_for_owner(owner_user_id, item_id)

    @staticmethod
    def item_for_response(item: LibraryItem) -> LibraryItem:
        if item.persistent_audio.status != "available":
            return item
        lesson = item.lesson.model_copy(
            update={"audio_url": f"/api/library/items/{item.id}/audio"}
        )
        return item.model_copy(update={"lesson": lesson})

    def _update_progress(
        self,
        owner_user_id: str,
        item_id: str,
        progress_value: Any,
        *,
        listening: bool,
    ) -> LibraryItem:
        self._validate_owner(owner_user_id)
        self._validate_item_id(item_id)
        try:
            with self._connect() as connection:
                self._assert_owner_provisioned(connection, owner_user_id)
                row = connection.execute(
                    self._item_select(
                        "WHERE item.owner_user_id = %s AND item.item_id = %s "
                        "FOR UPDATE OF item"
                    ),
                    (owner_user_id, item_id),
                ).fetchone()
                if row is None:
                    raise KeyError(item_id)
                item = self._row_to_item(row)
                if listening:
                    value = validated_listening_progress(progress_value, item.lesson)
                    column = "listening_progress_json"
                else:
                    value = validated_progress(progress_value, item.lesson)
                    column = "progress_json"
                connection.execute(
                    f"""
                    UPDATE cloud_library_items
                    SET {column} = %s, updated_at = %s, revision = revision + 1
                    WHERE owner_user_id = %s AND item_id = %s
                    """,
                    (
                        Jsonb(value.model_dump(mode="json")),
                        self._now(),
                        owner_user_id,
                        item_id,
                    ),
                )
                result = connection.execute(
                    self._item_select(
                        "WHERE item.owner_user_id = %s AND item.item_id = %s"
                    ),
                    (owner_user_id, item_id),
                ).fetchone()
        except (KeyError, ValueError):
            raise
        except psycopg.Error as exc:
            raise CloudLibraryUnavailableError("Cloud Library is unavailable") from exc
        if result is None:
            raise LibraryStoreError("Cloud Library update could not be read back")
        return self._row_to_item(result)

    def _connect(self):
        return psycopg.connect(self._dsn, row_factory=dict_row)

    def _record_cleanup_failure(self, cleanup_id: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE cloud_media_cleanup_outbox
                    SET attempt_count = attempt_count + 1, updated_at = %s
                    WHERE cleanup_id = %s
                    """,
                    (self._now(), cleanup_id),
                )
        except psycopg.Error:
            # The original outbox row was committed with the Library deletion.
            # Losing this best-effort counter update cannot erase that evidence.
            return

    def _complete_cleanup(self, cleanup_id: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM cloud_media_cleanup_outbox WHERE cleanup_id = %s",
                    (cleanup_id,),
                )
        except psycopg.Error:
            # A stale pending row is safe and retryable; never recreate access
            # metadata merely because acknowledgement failed.
            return

    @staticmethod
    def _assert_owner_provisioned(connection, owner_user_id: str) -> None:
        row = connection.execute(
            "SELECT 1 FROM cloud_users WHERE user_id = %s",
            (owner_user_id,),
        ).fetchone()
        if row is None:
            raise OwnerNotProvisionedError("Cloud Library owner is not provisioned")

    @staticmethod
    def _item_select(where: str) -> str:
        return f"""
            SELECT
                item.item_id, item.source_key, item.created_at, item.updated_at,
                item.lesson_json, item.progress_json,
                item.listening_progress_json, item.shadowing_progress_json,
                media.filename AS audio_filename,
                media.content_type AS audio_content_type
            FROM cloud_library_items AS item
            LEFT JOIN cloud_library_media_assets AS media
              ON media.owner_user_id = item.owner_user_id
             AND media.library_item_id = item.item_id
             AND media.asset_kind = 'audio'
             AND media.status = 'ready'
            {where}
        """

    @staticmethod
    def _row_to_item(row: dict[str, Any]) -> LibraryItem:
        filename = row.get("audio_filename")
        return LibraryItem.model_validate(
            {
                "id": str(row["item_id"]),
                "source_key": row["source_key"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "lesson": row["lesson_json"],
                "progress": row["progress_json"],
                "listening_progress": row["listening_progress_json"],
                "shadowing_progress": row["shadowing_progress_json"],
                "persistent_audio": PersistentAudio(
                    status="available" if filename else "unavailable",
                    filename=filename,
                    content_type=row.get("audio_content_type") if filename else None,
                ),
            }
        )

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise LibraryStoreError("Library clock must return a timezone-aware value")
        return value

    @staticmethod
    def _validate_owner(owner_user_id: str) -> None:
        if not SAFE_OWNER_ID.fullmatch(owner_user_id):
            raise LibraryStoreError("Invalid cloud Library owner")

    @staticmethod
    def _validate_item_id(item_id: str) -> None:
        if not SAFE_ITEM_ID.fullmatch(item_id):
            # Invalid and cross-owner resource IDs are deliberately identical.
            raise KeyError(item_id)
