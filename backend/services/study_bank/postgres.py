from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from services.review.scheduler import schedule_review
from services.schemas.study_bank import (
    MediaStudyProvenance,
    ReviewRating,
    ReviewState,
    StudyItem,
    StudyItemInput,
    StudyItemPatch,
)
from services.study_bank.store import (
    StudyBankConflictError,
    StudyBankStoreError,
    normalized_identity,
    utc_now,
)


SAFE_OWNER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_ITEM_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class StudyBankOwnerNotProvisionedError(StudyBankStoreError):
    """The authenticated owner has no trusted cloud user row."""


class CloudStudyBankUnavailableError(StudyBankStoreError):
    """Cloud persistence failed without leaking driver or connection details."""


class PostgresOwnerScopedStudyBankRepository:
    """Transactional Study Bank/Review repository with owner predicates throughout."""

    def __init__(
        self,
        dsn: str,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ):
        if not dsn:
            raise ValueError("A PostgreSQL DSN is required")
        self._dsn = dsn
        self.clock = clock
        self.id_factory = id_factory

    def list_items(
        self,
        owner_user_id: str,
        *,
        kind: str | None = None,
        language: str | None = None,
        active: bool | None = True,
        library_item_id: str | None = None,
        search: str | None = None,
    ) -> list[StudyItem]:
        self._validate_owner(owner_user_id)
        clauses = ["owner_user_id = %s"]
        values: list[object] = [owner_user_id]
        if kind is not None:
            clauses.append("kind = %s")
            values.append(kind)
        if language is not None:
            clauses.append("source_language = %s")
            values.append(language)
        if active is not None:
            clauses.append("active = %s")
            values.append(active)
        if library_item_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements(provenance_json) AS p "
                "WHERE p->>'source_type' = 'media' AND p->>'library_item_id' = %s)"
            )
            values.append(library_item_id)
        query = (search or "").strip()
        if query:
            clauses.append(
                "(text ILIKE %s OR meaning ILIKE %s OR EXISTS ("
                "SELECT 1 FROM jsonb_array_elements(provenance_json) AS p "
                "WHERE p->>'media_title' ILIKE %s))"
            )
            pattern = f"%{query}%"
            values.extend([pattern, pattern, pattern])
        try:
            with self._connect() as connection:
                self._set_owner_context(connection, owner_user_id)
                self._assert_owner_provisioned(connection, owner_user_id)
                rows = connection.execute(
                    f"{self._select_sql()} WHERE {' AND '.join(clauses)} "
                    "ORDER BY updated_at DESC, item_id DESC",
                    values,
                ).fetchall()
                return [self._row_to_item(row) for row in rows]
        except StudyBankOwnerNotProvisionedError:
            raise
        except psycopg.Error as exc:
            raise CloudStudyBankUnavailableError("Cloud Study Bank is unavailable") from exc

    def get_item(self, owner_user_id: str, item_id: str) -> StudyItem:
        self._validate_owner(owner_user_id)
        self._validate_item(item_id)
        try:
            with self._connect() as connection:
                self._set_owner_context(connection, owner_user_id)
                self._assert_owner_provisioned(connection, owner_user_id)
                row = connection.execute(
                    f"{self._select_sql()} WHERE owner_user_id = %s AND item_id = %s",
                    (owner_user_id, item_id),
                ).fetchone()
                if row is None:
                    raise KeyError(item_id)
                return self._row_to_item(row)
        except (KeyError, StudyBankOwnerNotProvisionedError):
            raise
        except psycopg.Error as exc:
            raise CloudStudyBankUnavailableError("Cloud Study Bank is unavailable") from exc

    def assert_readable(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1 FROM cloud_study_items LIMIT 1").fetchone()
        except psycopg.Error as exc:
            raise CloudStudyBankUnavailableError("Cloud Study Bank is unavailable") from exc

    def save_items_for_owner(
        self, owner_user_id: str, values: Iterable[StudyItemInput]
    ) -> tuple[list[StudyItem], int]:
        self._validate_owner(owner_user_id)
        prepared = list(values)
        if not prepared:
            return [], 0
        for value in prepared:
            if value.user_id != owner_user_id:
                raise ValueError("Study Bank owner does not match authenticated account")
            normalized_identity(value)
        results: list[StudyItem] = []
        created = 0
        try:
            with self._connect() as connection:
                self._set_owner_context(connection, owner_user_id)
                self._assert_owner_provisioned(connection, owner_user_id)
                for value in prepared:
                    result, was_created = self._upsert(connection, owner_user_id, value)
                    results.append(result)
                    created += int(was_created)
            return results, created
        except StudyBankOwnerNotProvisionedError:
            raise
        except psycopg.errors.ForeignKeyViolation as exc:
            if exc.diag.constraint_name == "cloud_study_media_library_fk":
                raise StudyBankConflictError(
                    "Authorized Library provenance changed before the Study Bank write"
                ) from exc
            raise StudyBankOwnerNotProvisionedError(
                "Cloud Study Bank owner is not provisioned"
            ) from exc
        except psycopg.Error as exc:
            raise CloudStudyBankUnavailableError("Cloud Study Bank is unavailable") from exc

    def update_item(
        self, owner_user_id: str, item_id: str, patch: StudyItemPatch
    ) -> StudyItem:
        self._validate_owner(owner_user_id)
        self._validate_item(item_id)
        try:
            with self._connect() as connection:
                self._set_owner_context(connection, owner_user_id)
                self._assert_owner_provisioned(connection, owner_user_id)
                row = connection.execute(
                    f"{self._select_sql()} WHERE owner_user_id = %s AND item_id = %s FOR UPDATE",
                    (owner_user_id, item_id),
                ).fetchone()
                if row is None:
                    raise KeyError(item_id)
                item = self._row_to_item(row)
                if (
                    patch.expected_revision is None
                    or patch.expected_revision != item.revision
                ):
                    raise StudyBankConflictError("Study Bank item state is stale")
                values = patch.model_dump(exclude_none=True, exclude={"expected_revision"})
                updated = connection.execute(
                    """
                    UPDATE cloud_study_items SET
                        active = %s, meaning = %s, note = %s,
                        updated_at = %s, revision = revision + 1
                    WHERE owner_user_id = %s AND item_id = %s
                    RETURNING *
                    """,
                    (
                        values.get("active", item.active),
                        values.get("meaning", item.meaning),
                        values.get("note", item.note),
                        self._now(),
                        owner_user_id,
                        item_id,
                    ),
                ).fetchone()
                return self._row_to_item(updated)
        except (KeyError, StudyBankConflictError, StudyBankOwnerNotProvisionedError):
            raise
        except psycopg.Error as exc:
            raise CloudStudyBankUnavailableError("Cloud Study Bank is unavailable") from exc

    def delete_item(
        self,
        owner_user_id: str,
        item_id: str,
        expected_revision: int | None = None,
    ) -> None:
        self._validate_owner(owner_user_id)
        self._validate_item(item_id)
        try:
            with self._connect() as connection:
                self._set_owner_context(connection, owner_user_id)
                self._assert_owner_provisioned(connection, owner_user_id)
                row = connection.execute(
                    """
                    SELECT revision FROM cloud_study_items
                    WHERE owner_user_id = %s AND item_id = %s FOR UPDATE
                    """,
                    (owner_user_id, item_id),
                ).fetchone()
                if row is None:
                    raise KeyError(item_id)
                if expected_revision is None or row["revision"] != expected_revision:
                    raise StudyBankConflictError("Study Bank item state is stale")
                deleted = connection.execute(
                    """
                    DELETE FROM cloud_study_items
                    WHERE owner_user_id = %s AND item_id = %s AND revision = %s
                    RETURNING item_id
                    """,
                    (owner_user_id, item_id, expected_revision),
                ).fetchone()
                if deleted is None:
                    raise StudyBankConflictError("Study Bank item state is stale")
        except (KeyError, StudyBankConflictError, StudyBankOwnerNotProvisionedError):
            raise
        except psycopg.Error as exc:
            raise CloudStudyBankUnavailableError("Cloud Study Bank is unavailable") from exc

    def rate_item(
        self,
        owner_user_id: str,
        item_id: str,
        rating: ReviewRating,
        expected_review_count: int,
    ) -> tuple[StudyItem, datetime]:
        self._validate_owner(owner_user_id)
        self._validate_item(item_id)
        try:
            with self._connect() as connection:
                self._set_owner_context(connection, owner_user_id)
                self._assert_owner_provisioned(connection, owner_user_id)
                row = connection.execute(
                    f"{self._select_sql()} WHERE owner_user_id = %s AND item_id = %s FOR UPDATE",
                    (owner_user_id, item_id),
                ).fetchone()
                if row is None:
                    raise KeyError(item_id)
                item = self._row_to_item(row)
                if not item.active:
                    raise StudyBankConflictError("Archived Study Bank items cannot be rated")
                if item.review.review_count != expected_review_count:
                    raise StudyBankConflictError("Study Bank review state is stale")
                now = self._now()
                previous_due = item.review.due_at
                review = schedule_review(item.review, rating, now)
                updated = connection.execute(
                    """
                    UPDATE cloud_study_items SET
                        due_at=%s, interval_seconds=%s, ease_factor=%s,
                        repetition_count=%s, lapse_count=%s, review_count=%s,
                        again_count=%s, hard_count=%s, good_count=%s, easy_count=%s,
                        last_reviewed_at=%s, last_rating=%s, updated_at=%s,
                        revision=revision + 1
                    WHERE owner_user_id=%s AND item_id=%s
                    RETURNING *
                    """,
                    (
                        review.due_at, review.interval_seconds, review.ease_factor,
                        review.repetition_count, review.lapse_count, review.review_count,
                        review.again_count, review.hard_count, review.good_count,
                        review.easy_count, review.last_reviewed_at, review.last_rating,
                        now, owner_user_id, item_id,
                    ),
                ).fetchone()
                return self._row_to_item(updated), previous_due
        except (KeyError, StudyBankConflictError, StudyBankOwnerNotProvisionedError):
            raise
        except psycopg.Error as exc:
            raise CloudStudyBankUnavailableError("Cloud Study Bank is unavailable") from exc

    def active_chunk_keys(self, owner_user_id: str, language: str) -> set[str]:
        return {
            item.normalized_text
            for item in self.list_items(
                owner_user_id, kind="chunk", language=language, active=True
            )
        }

    def _upsert(self, connection, owner: str, value: StudyItemInput) -> tuple[StudyItem, bool]:
        cleaned_text, normalized = normalized_identity(value)
        now = self._now()
        review = ReviewState.new(now)
        item_id = self.id_factory()
        inserted = connection.execute(
            """
            INSERT INTO cloud_study_items (
                owner_user_id, item_id, kind, source_language, target_language,
                text, normalized_text, meaning, lemma, part_of_speech, example,
                usage, note, provenance_json, active, created_at, updated_at,
                due_at, interval_seconds, ease_factor, repetition_count,
                lapse_count, review_count, again_count, hard_count, good_count,
                easy_count, last_reviewed_at, last_rating, revision
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1
            )
            ON CONFLICT (owner_user_id, kind, source_language, target_language, normalized_text)
            DO NOTHING RETURNING *
            """,
            (
                owner, item_id, value.kind, value.source_language, value.target_language,
                cleaned_text, normalized, value.meaning, value.lemma,
                value.part_of_speech, value.example, value.usage, value.note,
                Jsonb([p.model_dump(mode="json") for p in value.provenance]),
                now, now, review.due_at, review.interval_seconds, review.ease_factor,
                review.repetition_count, review.lapse_count, review.review_count,
                review.again_count, review.hard_count, review.good_count,
                review.easy_count, review.last_reviewed_at, review.last_rating,
            ),
        ).fetchone()
        if inserted is not None:
            item = self._row_to_item(inserted)
            self._sync_media_provenance(
                connection, owner, item.id, list(value.provenance), now
            )
            return item, True
        row = connection.execute(
            f"{self._select_sql()} WHERE owner_user_id=%s AND kind=%s "
            "AND source_language=%s AND target_language=%s AND normalized_text=%s FOR UPDATE",
            (owner, value.kind, value.source_language, value.target_language, normalized),
        ).fetchone()
        existing = self._row_to_item(row)
        provenance = list(existing.provenance)
        fingerprints = {p.model_dump_json() for p in provenance}
        for source in value.provenance:
            if source.model_dump_json() not in fingerprints:
                provenance.append(source)
                fingerprints.add(source.model_dump_json())
        if len(provenance) > 1_000:
            raise ValueError("Study Bank provenance limit exceeded")
        fields = {
            "meaning": existing.meaning or value.meaning,
            "lemma": existing.lemma or value.lemma,
            "part_of_speech": existing.part_of_speech or value.part_of_speech,
            "example": existing.example or value.example,
            "usage": existing.usage or value.usage,
            "note": existing.note or value.note,
        }
        changed = provenance != list(existing.provenance) or any(
            getattr(existing, key) != result for key, result in fields.items()
        )
        if not changed:
            self._sync_media_provenance(
                connection, owner, existing.id, provenance, now
            )
            return existing, False
        updated = connection.execute(
            """
            UPDATE cloud_study_items SET provenance_json=%s, meaning=%s, lemma=%s,
                part_of_speech=%s, example=%s, usage=%s, note=%s,
                updated_at=%s, revision=revision + 1
            WHERE owner_user_id=%s AND item_id=%s RETURNING *
            """,
            (
                Jsonb([p.model_dump(mode="json") for p in provenance]),
                fields["meaning"], fields["lemma"], fields["part_of_speech"],
                fields["example"], fields["usage"], fields["note"], now,
                owner, existing.id,
            ),
        ).fetchone()
        result = self._row_to_item(updated)
        self._sync_media_provenance(
            connection, owner, result.id, provenance, now
        )
        return result, False

    @staticmethod
    def _sync_media_provenance(
        connection,
        owner: str,
        item_id: str,
        provenance: list,
        now: datetime,
    ) -> None:
        for index, source in enumerate(provenance):
            if not isinstance(source, MediaStudyProvenance):
                continue
            connection.execute(
                """
                INSERT INTO cloud_study_media_provenance (
                    owner_user_id, study_item_id, provenance_index,
                    library_item_id, created_at
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (owner_user_id, study_item_id, provenance_index)
                DO NOTHING
                """,
                (owner, item_id, index, source.library_item_id, now),
            )

    @staticmethod
    def _select_sql() -> str:
        return "SELECT * FROM cloud_study_items"

    @staticmethod
    def _row_to_item(row) -> StudyItem:
        review = ReviewState(
            due_at=row["due_at"], interval_seconds=row["interval_seconds"],
            ease_factor=row["ease_factor"], repetition_count=row["repetition_count"],
            lapse_count=row["lapse_count"], review_count=row["review_count"],
            again_count=row["again_count"], hard_count=row["hard_count"],
            good_count=row["good_count"], easy_count=row["easy_count"],
            last_reviewed_at=row["last_reviewed_at"], last_rating=row["last_rating"],
        )
        return StudyItem.model_validate({
            "id": str(row["item_id"]), "user_id": row["owner_user_id"],
            "kind": row["kind"], "source_language": row["source_language"],
            "target_language": row["target_language"], "text": row["text"],
            "normalized_text": row["normalized_text"], "meaning": row["meaning"],
            "lemma": row["lemma"], "part_of_speech": row["part_of_speech"],
            "example": row["example"], "usage": row["usage"], "note": row["note"],
            "provenance": row["provenance_json"], "active": row["active"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "review": review, "revision": row["revision"],
        })

    def _connect(self):
        return psycopg.connect(self._dsn, row_factory=dict_row)

    @staticmethod
    def _assert_owner_provisioned(connection, owner: str) -> None:
        if connection.execute(
            "SELECT 1 FROM cloud_users WHERE user_id=%s", (owner,)
        ).fetchone() is None:
            raise StudyBankOwnerNotProvisionedError(
                "Cloud Study Bank owner is not provisioned"
            )

    @staticmethod
    def _set_owner_context(connection, owner: str) -> None:
        connection.execute(
            "SELECT set_config('app.current_owner', %s, true)", (owner,)
        )

    @staticmethod
    def _validate_owner(owner: str) -> None:
        if not SAFE_OWNER_ID.fullmatch(owner):
            raise StudyBankStoreError("Invalid Study Bank owner")

    @staticmethod
    def _validate_item(item_id: str) -> None:
        if not SAFE_ITEM_ID.fullmatch(item_id):
            raise KeyError(item_id)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise StudyBankStoreError("Study Bank clock must be timezone-aware")
        return value.astimezone(timezone.utc)
