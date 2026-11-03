from __future__ import annotations

import hashlib
import hmac
import logging
import mimetypes
import os
import re
import stat
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

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
VERIFY_CHUNK_BYTES = 1024 * 1024
ROOT_MARKER = ".language-ai-media-root"
# An upload records its new object key in the cleanup outbox before writing
# bytes; the worker leaves that row alone for this long so a slow upload can
# still adopt the key. A crash during upload is cleaned after the grace.
UPLOAD_CLEANUP_GRACE = timedelta(minutes=15)
CLEANUP_BACKOFF_BASE = timedelta(seconds=30)
CLEANUP_BACKOFF_CAP = timedelta(hours=6)
MAX_CLEANUP_ATTEMPTS = 10
MAX_CLEANUP_BATCH = 500
CLEANUP_OUTCOMES = ("claimed", "deleted", "retry_scheduled", "dead", "skipped")

logger = logging.getLogger(__name__)


class OwnerNotProvisionedError(LibraryStoreError):
    """The trusted principal has no corresponding server-owned user row."""


class CloudLibraryUnavailableError(LibraryStoreError):
    """Cloud persistence failed without exposing driver or connection details."""


class MediaIntegrityError(LibraryStoreError):
    """Stored bytes do not match their metadata; nothing may be served."""


class MediaNamespaceError(LibraryStoreError):
    """An object key does not belong to the owner/item it is used for."""


class MediaRootUnavailableError(LibraryStoreError):
    """The configured object root is missing or was never initialised."""


class MediaUploadAbortedError(LibraryStoreError):
    """The new object was claimed for cleanup before its metadata committed."""


class FilesystemMediaObjectStore:
    """Test/development object adapter; never exposed as a static directory.

    Production object-storage selection and signed delivery remain deferred.  The
    adapter exists so tenant metadata and byte authorization can be exercised
    end-to-end without treating the Personal Desktop media directory as cloud
    storage.
    """

    def __init__(self, root: str | Path, *, create: bool = True):
        self.root = Path(root)
        marker = self.root / ROOT_MARKER
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
            marker.touch(exist_ok=True)
        elif not marker.is_file():
            # A wrong or unmounted root would make every delete a no-op
            # "success" and silently empty the cleanup outbox.
            raise MediaRootUnavailableError("Media root is not initialised")
        self._root = self.root.resolve()

    def put_audio(
        self, owner_user_id: str, item_id: str, filename: str, content: bytes
    ) -> str:
        object_key = self.new_audio_key(owner_user_id, item_id, filename)
        self.write_audio(object_key, owner_user_id, item_id, content)
        return object_key

    @staticmethod
    def new_audio_key(owner_user_id: str, item_id: str, filename: str) -> str:
        """Server-generated, never-reused key inside the owner/item namespace."""

        if not SAFE_OWNER_ID.fullmatch(owner_user_id):
            raise LibraryStoreError("Invalid media owner")
        if not SAFE_ITEM_ID.fullmatch(item_id):
            raise LibraryStoreError("Invalid Library item id")
        suffix = Path(filename).suffix.lower()
        if not suffix or len(suffix) > 10 or not re.fullmatch(r"\.[a-z0-9]+", suffix):
            suffix = ".bin"
        owner_key = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
        return (
            f"owners/{owner_key}/library/{item_id}/"
            f"audio-{uuid.uuid4().hex}{suffix}"
        )

    def write_audio(
        self, object_key: str, owner_user_id: str, item_id: str, content: bytes
    ) -> None:
        self._validate_namespace(object_key, owner_user_id, item_id)
        if not content or len(content) > MAX_AUDIO_BYTES:
            raise LibraryStoreError("Invalid Library audio size")
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

    def open_verified(
        self,
        object_key: str,
        owner_user_id: str,
        item_id: str,
        *,
        byte_size: int,
        sha256: str,
    ) -> BinaryIO:
        """Open an object and prove its bytes match the stored size and hash.

        Reads at most ``MAX_AUDIO_BYTES`` in ``VERIFY_CHUNK_BYTES`` chunks, so
        memory stays bounded whatever is on disk. Returns the hashed handle,
        rewound; mismatches raise ``MediaIntegrityError`` and serve nothing.
        """

        self._validate_namespace(object_key, owner_user_id, item_id)
        path = self._safe_path(object_key)
        try:
            handle = open(
                path,
                "rb",
                # O_NONBLOCK: opening a FIFO planted at the key would
                # otherwise block until a writer appears.
                opener=lambda name, flags: os.open(
                    name,
                    flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                ),
            )
        except FileNotFoundError as exc:
            raise KeyError(object_key) from exc
        except OSError as exc:
            # A symlink planted at the key fails here with ELOOP.
            raise MediaIntegrityError("integrity") from exc
        try:
            status = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(status.st_mode)
                or not 0 < byte_size <= MAX_AUDIO_BYTES
                or status.st_size != byte_size
            ):
                raise MediaIntegrityError("integrity")
            digest = hashlib.sha256()
            remaining = byte_size + 1
            while remaining:
                chunk = handle.read(min(VERIFY_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                remaining -= len(chunk)
            if remaining != 1 or not hmac.compare_digest(digest.hexdigest(), sha256):
                raise MediaIntegrityError("integrity")
            handle.seek(0)
        except BaseException:
            handle.close()
            raise
        return handle

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
            raise MediaNamespaceError("Unsafe media object key")
        literal = self._root.joinpath(*object_key.split("/"))
        try:
            candidate = literal.resolve()
        except (OSError, RuntimeError) as exc:
            # A symlink loop: RuntimeError on Python 3.11, OSError on 3.12+.
            raise MediaNamespaceError("Unsafe media object key") from exc
        # Any symlink along the key (a swapped directory or object) makes the
        # resolved path differ from the literal one.
        if candidate != literal:
            raise MediaNamespaceError("Unsafe media object key")
        return candidate

    @staticmethod
    def _validate_namespace(
        object_key: str, owner_user_id: str, item_id: str
    ) -> None:
        if not SAFE_OWNER_ID.fullmatch(owner_user_id) or not SAFE_ITEM_ID.fullmatch(
            item_id
        ):
            raise MediaNamespaceError("Invalid media ownership namespace")
        owner_key = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
        pattern = re.compile(
            rf"^owners/{owner_key}/library/{re.escape(item_id)}/"
            r"audio-[0-9a-f]{32}\.[a-z0-9]+$"
        )
        if not pattern.fullmatch(object_key):
            raise MediaNamespaceError("Media object ownership does not match metadata")

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
        cleanup_ids: list[str] = []
        try:
            with self._connect() as connection:
                self._assert_owner_provisioned(connection, owner_user_id)
                # Locking the item first serializes with an upload's first
                # media INSERT (its foreign-key check needs KEY SHARE), so no
                # object can be attached after the keys below were read.
                locked = connection.execute(
                    """
                    SELECT 1 FROM cloud_library_items
                    WHERE owner_user_id = %s AND item_id = %s
                    FOR UPDATE
                    """,
                    (owner_user_id, item_id),
                ).fetchone()
                if locked is None:
                    raise KeyError(item_id)
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
                    cleanup_ids.append(
                        self._enqueue_cleanup(
                            connection,
                            owner_user_id,
                            item_id,
                            object_key,
                            not_before=self._now(),
                        )
                    )
                connection.execute(
                    """
                    DELETE FROM cloud_library_items
                    WHERE owner_user_id = %s AND item_id = %s
                    """,
                    (owner_user_id, item_id),
                )
        except KeyError:
            raise
        except psycopg.Error as exc:
            raise CloudLibraryUnavailableError("Cloud Library is unavailable") from exc
        # Metadata is already inaccessible. The transaction retained a durable
        # cleanup record before deleting it, so object-store failures are
        # recoverable without keeping the Library item visible.
        self._cleanup_now(cleanup_ids)

    def audio_for_owner(self, owner_user_id: str, item_id: str) -> OwnerScopedAudio:
        self._validate_owner(owner_user_id)
        self._validate_item_id(item_id)
        try:
            with self._connect() as connection:
                self._assert_owner_provisioned(connection, owner_user_id)
                row = connection.execute(
                    """
                    SELECT media.object_key, media.content_type, media.byte_size,
                           media.sha256
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
        try:
            handle = self.media_objects.open_verified(
                row["object_key"],
                owner_user_id,
                item_id,
                byte_size=row["byte_size"],
                sha256=row["sha256"],
            )
        except (MediaIntegrityError, MediaNamespaceError) as exc:
            # Log the item only; object keys and paths stay out of logs too.
            logger.warning(
                "cloud library audio failed integrity check item=%s reason=%s",
                item_id,
                type(exc).__name__,
            )
            raise MediaIntegrityError("Library audio failed an integrity check") from exc
        return OwnerScopedAudio(
            path=Path(handle.name),
            content_type=row["content_type"],
            file=handle,
            byte_size=row["byte_size"],
            sha256=row["sha256"],
        )

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

        The new key is recorded in the cleanup outbox before any byte is
        written, so a crash at any point leaves a row the worker can act on.
        The metadata transaction adopts the key by deleting that row and, in
        the same commit, enqueues the replaced key; it never deletes an old
        object itself.
        """

        self.get_for_owner(owner_user_id, item_id)
        if not content or len(content) > MAX_AUDIO_BYTES:
            raise LibraryStoreError("Invalid Library audio size")
        object_key = self.media_objects.new_audio_key(owner_user_id, item_id, filename)
        try:
            with self._connect() as connection:
                upload_cleanup_id = self._enqueue_cleanup(
                    connection,
                    owner_user_id,
                    item_id,
                    object_key,
                    not_before=self._now() + UPLOAD_CLEANUP_GRACE,
                )
        except psycopg.Error as exc:
            raise CloudLibraryUnavailableError("Cloud Library is unavailable") from exc
        replaced_cleanup_id: str | None = None
        try:
            self.media_objects.write_audio(object_key, owner_user_id, item_id, content)
            with self._connect() as connection:
                # Item lock first, matching delete_for_owner. Without it two
                # first uploads both see no media row, and the second upsert
                # overwrites the first key without enqueueing it.
                locked = connection.execute(
                    """
                    SELECT 1 FROM cloud_library_items
                    WHERE owner_user_id = %s AND item_id = %s
                    FOR NO KEY UPDATE
                    """,
                    (owner_user_id, item_id),
                ).fetchone()
                if locked is None:
                    raise KeyError(item_id)
                adopted = connection.execute(
                    """
                    DELETE FROM cloud_media_cleanup_outbox
                    WHERE cleanup_id = %s AND object_key = %s AND status = 'pending'
                    RETURNING cleanup_id
                    """,
                    (upload_cleanup_id, object_key),
                ).fetchone()
                if adopted is None:
                    # A worker claimed and removed the object after the grace.
                    raise MediaUploadAbortedError("Library audio upload expired")
                previous = connection.execute(
                    """
                    SELECT object_key FROM cloud_library_media_assets
                    WHERE owner_user_id = %s AND library_item_id = %s
                      AND asset_kind = 'audio'
                    FOR UPDATE
                    """,
                    (owner_user_id, item_id),
                ).fetchone()
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
                        str(uuid.uuid4()),
                        item_id,
                        object_key,
                        Path(filename).name[:255] or "audio.bin",
                        content_type
                        or mimetypes.guess_type(filename)[0]
                        or "application/octet-stream",
                        len(content),
                        hashlib.sha256(content).hexdigest(),
                        self._now(),
                        self._now(),
                    ),
                )
                if previous is not None and previous["object_key"] != object_key:
                    replaced_cleanup_id = self._enqueue_cleanup(
                        connection,
                        owner_user_id,
                        item_id,
                        previous["object_key"],
                        not_before=self._now(),
                    )
        except BaseException as exc:
            # The upload row is still pending (its adoption rolled back), so
            # the new object is reclaimed now or by a later worker run.
            cleanup_ids = [upload_cleanup_id]
            if isinstance(exc, MediaUploadAbortedError):
                # A worker took the row, possibly before os.replace landed the
                # object. The key was never adopted, so track it again.
                cleanup_ids = self._reenqueue(owner_user_id, item_id, object_key)
            self._cleanup_now(cleanup_ids)
            if isinstance(exc, psycopg.errors.ForeignKeyViolation):
                raise KeyError(item_id) from exc
            if isinstance(exc, psycopg.Error):
                raise CloudLibraryUnavailableError("Cloud Library is unavailable") from exc
            raise
        if replaced_cleanup_id is not None:
            self._cleanup_now([replaced_cleanup_id])
        return self.get_for_owner(owner_user_id, item_id)

    def run_cleanup_batch(self, *, limit: int = 50) -> dict[str, int]:
        """Claim up to ``limit`` due outbox rows and try each once.

        Safe with any number of concurrent workers: claims use SKIP LOCKED and
        each deletion runs under the row lock of the claim it was given.
        """

        if not 1 <= limit <= MAX_CLEANUP_BATCH:
            raise ValueError("Cleanup batch out of range")
        counts = {outcome: 0 for outcome in CLEANUP_OUTCOMES}
        claims = self._claim_cleanups(limit=limit)
        counts["claimed"] = len(claims)
        for claim in claims:
            counts[self._process_cleanup(claim)] += 1
        return counts

    def cleanup_backlog(self) -> dict[str, int]:
        """Counts for monitoring; contains no keys, owners or paths."""

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        count(*) FILTER (WHERE status = 'pending') AS pending,
                        count(*) FILTER (
                            WHERE status = 'pending' AND next_attempt_at <= %s
                        ) AS due,
                        count(*) FILTER (WHERE status = 'dead') AS dead
                    FROM cloud_media_cleanup_outbox
                    """,
                    (self._now(),),
                ).fetchone()
        except psycopg.Error as exc:
            raise CloudLibraryUnavailableError("Cloud Library is unavailable") from exc
        return {key: int(row[key]) for key in ("pending", "due", "dead")}

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

    def _enqueue_cleanup(
        self,
        connection,
        owner_user_id: str,
        item_id: str,
        object_key: str,
        *,
        not_before: datetime,
    ) -> str:
        now = self._now()
        row = connection.execute(
            """
            INSERT INTO cloud_media_cleanup_outbox (
                cleanup_id, owner_user_id, library_item_id, object_key,
                status, attempt_count, created_at, updated_at, next_attempt_at
            ) VALUES (%s, %s, %s, %s, 'pending', 0, %s, %s, %s)
            ON CONFLICT (object_key) DO UPDATE SET
                updated_at = GREATEST(
                    cloud_media_cleanup_outbox.updated_at, EXCLUDED.updated_at
                )
            RETURNING cleanup_id
            """,
            (
                str(uuid.uuid4()),
                owner_user_id,
                item_id,
                object_key,
                now,
                now,
                not_before,
            ),
        ).fetchone()
        return str(row["cleanup_id"])

    def _reenqueue(self, owner_user_id: str, item_id: str, object_key: str) -> list[str]:
        try:
            with self._connect() as connection:
                return [
                    self._enqueue_cleanup(
                        connection,
                        owner_user_id,
                        item_id,
                        object_key,
                        not_before=self._now(),
                    )
                ]
        except psycopg.Error:
            logger.error(
                "cloud media upload abort could not be re-enqueued item=%s", item_id
            )
            return []

    def _cleanup_now(self, cleanup_ids: list[str]) -> None:
        """Best-effort immediate cleanup; any failure leaves a durable row."""

        if not cleanup_ids:
            return
        try:
            for claim in self._claim_cleanups(
                limit=len(cleanup_ids), cleanup_ids=cleanup_ids
            ):
                self._process_cleanup(claim)
        except CloudLibraryUnavailableError:
            return

    def _claim_cleanups(
        self, *, limit: int, cleanup_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Claim rows by bumping attempt_count and pushing next_attempt_at.

        The claim commits on its own, so a crash after it counts as an attempt
        and the row comes back after its backoff. Named rows are claimed even
        when not yet due (immediate cleanup after a commit or a failed upload).
        """

        now = self._now()
        if cleanup_ids is None:
            due = "next_attempt_at <= %(now)s"
        else:
            due = "cleanup_id = ANY(%(ids)s::uuid[])"
        try:
            with self._connect() as connection:
                return connection.execute(
                    f"""
                    UPDATE cloud_media_cleanup_outbox AS outbox
                    SET attempt_count = outbox.attempt_count + 1,
                        updated_at = GREATEST(outbox.updated_at, %(now)s),
                        next_attempt_at = %(now)s + make_interval(
                            secs => LEAST(
                                %(base)s * power(2, LEAST(outbox.attempt_count, 30)),
                                %(cap)s
                            )
                        )
                    FROM (
                        SELECT cleanup_id FROM cloud_media_cleanup_outbox
                        WHERE status = 'pending' AND {due}
                        ORDER BY next_attempt_at, cleanup_id
                        LIMIT %(limit)s
                        FOR UPDATE SKIP LOCKED
                    ) AS claimable
                    WHERE outbox.cleanup_id = claimable.cleanup_id
                    RETURNING outbox.cleanup_id, outbox.owner_user_id,
                              outbox.library_item_id, outbox.object_key,
                              outbox.attempt_count
                    """,
                    {
                        "now": now,
                        "ids": cleanup_ids,
                        "limit": limit,
                        "base": CLEANUP_BACKOFF_BASE.total_seconds(),
                        "cap": CLEANUP_BACKOFF_CAP.total_seconds(),
                    },
                ).fetchall()
        except psycopg.Error as exc:
            raise CloudLibraryUnavailableError("Cloud Library is unavailable") from exc

    def _process_cleanup(self, claim: dict[str, Any]) -> str:
        """Delete one claimed object while holding its outbox row lock.

        The lock is what makes a stale claim harmless: an upload adopting the
        same key must delete this row first, so it either waits and then finds
        the row gone (and aborts), or it commits first and this finds nothing.
        Owner, item and key all come from the row, never from a caller.
        """

        cleanup_id = str(claim["cleanup_id"])
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT owner_user_id, library_item_id, object_key, attempt_count
                    FROM cloud_media_cleanup_outbox
                    WHERE cleanup_id = %s AND attempt_count = %s
                      AND status = 'pending'
                    FOR UPDATE SKIP LOCKED
                    """,
                    (cleanup_id, claim["attempt_count"]),
                ).fetchone()
                if row is None:
                    # Adopted by its upload, re-claimed, or already done.
                    return "skipped"
                referenced = connection.execute(
                    "SELECT 1 FROM cloud_library_media_assets WHERE object_key = %s",
                    (row["object_key"],),
                ).fetchone()
                if referenced is not None:
                    return self._mark_dead(
                        connection, cleanup_id, "object_still_referenced"
                    )
                try:
                    self.media_objects.delete(
                        row["object_key"],
                        row["owner_user_id"],
                        str(row["library_item_id"]),
                    )
                except MediaNamespaceError:
                    return self._mark_dead(connection, cleanup_id, "namespace_mismatch")
                except Exception as exc:
                    # Any other object-store error (including unexpected
                    # OSError/RuntimeError) is a retry; only its class is
                    # logged, since messages can carry absolute paths.
                    logger.warning(
                        "cloud media cleanup delete failed cleanup_id=%s error=%s",
                        cleanup_id,
                        type(exc).__name__,
                    )
                    if row["attempt_count"] >= MAX_CLEANUP_ATTEMPTS:
                        return self._mark_dead(
                            connection, cleanup_id, "object_delete_failed"
                        )
                    connection.execute(
                        """
                        UPDATE cloud_media_cleanup_outbox
                        SET last_error_code = 'object_delete_failed'
                        WHERE cleanup_id = %s
                        """,
                        (cleanup_id,),
                    )
                    logger.warning(
                        "cloud media cleanup will retry cleanup_id=%s attempt=%s",
                        cleanup_id,
                        row["attempt_count"],
                    )
                    return "retry_scheduled"
                connection.execute(
                    "DELETE FROM cloud_media_cleanup_outbox WHERE cleanup_id = %s",
                    (cleanup_id,),
                )
                return "deleted"
        except psycopg.Error as exc:
            # Rolled back: the claim stays committed and the row retries later.
            raise CloudLibraryUnavailableError("Cloud Library is unavailable") from exc

    @staticmethod
    def _mark_dead(connection, cleanup_id: str, reason: str) -> str:
        connection.execute(
            """
            UPDATE cloud_media_cleanup_outbox
            SET status = 'dead', last_error_code = %s
            WHERE cleanup_id = %s
            """,
            (reason, cleanup_id),
        )
        logger.error(
            "cloud media cleanup needs an operator cleanup_id=%s reason=%s",
            cleanup_id,
            reason,
        )
        return "dead"

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
