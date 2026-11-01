from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from services.ai_chunks.store import CandidateConflictError, CandidateNotFoundError
from services.schemas.ai_chunks import (
    AiChunkBatch,
    AiChunkCandidate,
    CandidateDecisionResponse,
    CandidateEvidence,
)
from services.schemas.lesson import Lesson
from services.schemas.study_bank import StudyItemInput
from services.study_bank.postgres import (
    CloudStudyBankUnavailableError,
    PostgresOwnerScopedStudyBankRepository,
    StudyBankOwnerNotProvisionedError,
)
from services.study_bank.store import StudyBankConflictError, utc_now


SAFE_OWNER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
SAFE_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class CloudAiChunkUnavailableError(RuntimeError):
    """Cloud candidate persistence failed without leaking driver details."""


class CloudAiChunkOwnerNotProvisionedError(CloudAiChunkUnavailableError):
    pass


@dataclass(frozen=True, slots=True)
class GenerationReservation:
    lease_token: str | None = None
    existing_batch: AiChunkBatch | None = None


@dataclass(frozen=True, slots=True)
class PreparedDecision:
    candidate: AiChunkCandidate
    study_input: StudyItemInput | None


class PostgresOwnerScopedAiChunkRepository:
    """Durable candidate storage and atomic Study Bank decision boundary."""

    def __init__(
        self,
        dsn: str,
        study_bank: PostgresOwnerScopedStudyBankRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
        lease_seconds: int = 30,
        retention_days: int = 30,
        max_pending_per_owner: int = 10,
    ):
        if not dsn:
            raise ValueError("A PostgreSQL DSN is required")
        if lease_seconds < 1 or retention_days < 1 or max_pending_per_owner < 1:
            raise ValueError("Cloud candidate limits must be positive")
        self._dsn = dsn
        self.study_bank = study_bank
        self.clock = clock
        self.id_factory = id_factory
        self.lease_seconds = lease_seconds
        self.retention_days = retention_days
        self.max_pending_per_owner = max_pending_per_owner

    @staticmethod
    def fingerprint(value: object) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def reserve_generation(
        self, owner: str, key: str, fingerprint: str
    ) -> GenerationReservation:
        self._validate_owner(owner)
        self._validate_key(key)
        self._validate_fingerprint(fingerprint)
        now = self._now()
        token = self.id_factory()
        try:
            with self._connect() as connection:
                self._set_owner_context(connection, owner)
                self._assert_owner(connection, owner)
                # Serializes the bounded pending-count check across workers.
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (owner,)
                )
                inserted = connection.execute(
                    """
                    INSERT INTO cloud_ai_chunk_generation_requests (
                        owner_user_id, idempotency_key, request_fingerprint,
                        status, lease_token, lease_expires_at,
                        created_at, updated_at, expires_at
                    )
                    SELECT %s,%s,%s,'pending',%s,%s,%s,%s,%s
                    WHERE (
                        SELECT count(*) FROM cloud_ai_chunk_generation_requests
                        WHERE owner_user_id=%s AND status='pending' AND lease_expires_at>%s
                    ) < %s
                    ON CONFLICT DO NOTHING
                    RETURNING idempotency_key
                    """,
                    (
                        owner,
                        key,
                        fingerprint,
                        token,
                        now + timedelta(seconds=self.lease_seconds),
                        now,
                        now,
                        now + timedelta(days=self.retention_days),
                        owner,
                        now,
                        self.max_pending_per_owner,
                    ),
                ).fetchone()
                row = connection.execute(
                    """
                    SELECT * FROM cloud_ai_chunk_generation_requests
                    WHERE owner_user_id=%s AND idempotency_key=%s FOR UPDATE
                    """,
                    (owner, key),
                ).fetchone()
                if row is None:
                    raise CandidateConflictError("Too many candidate requests are in progress")
                if row["request_fingerprint"] != fingerprint:
                    raise CandidateConflictError(
                        "Idempotency key was already used for another request"
                    )
                if row["status"] == "completed":
                    return GenerationReservation(
                        existing_batch=self._load_batch(connection, owner, str(row["batch_id"]))
                    )
                if inserted is not None:
                    return GenerationReservation(lease_token=token)
                if row["lease_expires_at"] > now:
                    raise CandidateConflictError("Candidate generation is already in progress")
                reclaimed = connection.execute(
                    """
                    UPDATE cloud_ai_chunk_generation_requests SET
                        lease_token=%s, lease_expires_at=%s, updated_at=%s,
                        expires_at=%s
                    WHERE owner_user_id=%s AND idempotency_key=%s AND status='pending'
                    RETURNING idempotency_key
                    """,
                    (
                        token,
                        now + timedelta(seconds=self.lease_seconds),
                        now,
                        now + timedelta(days=self.retention_days),
                        owner,
                        key,
                    ),
                ).fetchone()
                if reclaimed is None:
                    raise CandidateConflictError("Candidate request state changed")
                return GenerationReservation(lease_token=token)
        except (CandidateConflictError, CloudAiChunkOwnerNotProvisionedError):
            raise
        except psycopg.Error as exc:
            raise CloudAiChunkUnavailableError("Cloud candidate storage is unavailable") from exc

    def abandon_generation(
        self, owner: str, key: str, fingerprint: str, lease_token: str
    ) -> None:
        """Release only this worker's unfinished reservation for safe retry."""
        now = self._now()
        try:
            with self._connect() as connection:
                self._set_owner_context(connection, owner)
                connection.execute(
                    """
                    UPDATE cloud_ai_chunk_generation_requests
                    SET lease_expires_at=%s, updated_at=%s
                    WHERE owner_user_id=%s AND idempotency_key=%s
                      AND request_fingerprint=%s AND status='pending' AND lease_token=%s
                    """,
                    (now, now, owner, key, fingerprint, lease_token),
                )
        except psycopg.Error as exc:
            raise CloudAiChunkUnavailableError("Cloud candidate storage is unavailable") from exc

    def complete_generation(
        self,
        owner: str,
        key: str,
        fingerprint: str,
        lease_token: str,
        batch: AiChunkBatch,
    ) -> AiChunkBatch:
        self._validate_owner(owner)
        now = self._now()
        expires_at = now + timedelta(days=self.retention_days)
        durable = batch.model_copy(update={"expires_at": expires_at, "status": "ready"})
        if durable.user_id != owner:
            raise ValueError("Candidate batch owner does not match authenticated account")
        if any(value.source_language != durable.source_language for value in durable.candidates):
            raise ValueError("Candidate language does not match its batch")
        try:
            with self._connect() as connection:
                self._set_owner_context(connection, owner)
                self._assert_owner(connection, owner)
                request = connection.execute(
                    """
                    SELECT * FROM cloud_ai_chunk_generation_requests
                    WHERE owner_user_id=%s AND idempotency_key=%s FOR UPDATE
                    """,
                    (owner, key),
                ).fetchone()
                if request is None or request["request_fingerprint"] != fingerprint:
                    raise CandidateConflictError("Candidate request reservation is unavailable")
                if request["status"] == "completed":
                    return self._load_batch(connection, owner, str(request["batch_id"]))
                if str(request["lease_token"]) != lease_token:
                    raise CandidateConflictError("Candidate request lease is stale")
                connection.execute(
                    """
                    INSERT INTO cloud_ai_chunk_batches (
                        owner_user_id,batch_id,source_language,source_type,
                        source_label,created_at,expires_at,status
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,'ready')
                    """,
                    (
                        owner,
                        durable.batch_id,
                        durable.source_language,
                        durable.source_type,
                        durable.source_label,
                        durable.created_at,
                        expires_at,
                    ),
                )
                for ordinal, candidate in enumerate(durable.candidates):
                    self._validate_library_evidence(connection, owner, candidate)
                    self._insert_candidate(connection, owner, durable.batch_id, ordinal, candidate)
                updated = connection.execute(
                    """
                    UPDATE cloud_ai_chunk_generation_requests SET
                        status='completed', batch_id=%s, lease_token=NULL,
                        lease_expires_at=NULL, updated_at=%s, expires_at=%s
                    WHERE owner_user_id=%s AND idempotency_key=%s
                      AND status='pending' AND lease_token=%s
                    RETURNING batch_id
                    """,
                    (durable.batch_id, now, expires_at, owner, key, lease_token),
                ).fetchone()
                if updated is None:
                    raise CandidateConflictError("Candidate request lease is stale")
                return self._load_batch(connection, owner, durable.batch_id)
        except (CandidateConflictError, CloudAiChunkOwnerNotProvisionedError):
            raise
        except psycopg.errors.ForeignKeyViolation as exc:
            raise CandidateConflictError(
                "Authorized Library source changed before candidates were saved"
            ) from exc
        except psycopg.Error as exc:
            raise CloudAiChunkUnavailableError("Cloud candidate storage is unavailable") from exc

    def get_batch(self, owner: str, batch_id: str) -> AiChunkBatch:
        self._validate_owner(owner)
        self._validate_uuid(batch_id)
        try:
            with self._connect() as connection:
                self._set_owner_context(connection, owner)
                self._assert_owner(connection, owner)
                return self._load_batch(connection, owner, batch_id)
        except (CandidateNotFoundError, CloudAiChunkOwnerNotProvisionedError):
            raise
        except psycopg.Error as exc:
            raise CloudAiChunkUnavailableError("Cloud candidate storage is unavailable") from exc

    def decide_atomic(
        self,
        owner: str,
        batch_id: str,
        candidate_id: str,
        expected_version: int,
        *,
        action: str,
        decision_key: str,
        decision_fingerprint: str,
        prepare: Callable[[AiChunkCandidate], PreparedDecision],
    ) -> CandidateDecisionResponse:
        self._validate_owner(owner)
        self._validate_uuid(batch_id)
        self._validate_uuid(candidate_id)
        self._validate_key(decision_key)
        self._validate_fingerprint(decision_fingerprint)
        now = self._now()
        try:
            with self._connect() as connection:
                self._set_owner_context(connection, owner)
                self._assert_owner(connection, owner)
                # Serialize this idempotency key across workers so a concurrent
                # replay observes the committed durable response rather than a
                # transient stale-candidate conflict.
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 1))",
                    (f"{owner}:{decision_key}",),
                )
                replay = connection.execute(
                    """
                    SELECT keyrow.decision_fingerprint, decision.response_json
                    FROM cloud_ai_chunk_decision_keys AS keyrow
                    JOIN cloud_ai_chunk_decisions AS decision
                      ON decision.owner_user_id=keyrow.owner_user_id
                     AND decision.decision_id=keyrow.decision_id
                    WHERE keyrow.owner_user_id=%s AND keyrow.idempotency_key=%s
                    """,
                    (owner, decision_key),
                ).fetchone()
                if replay is not None:
                    if replay["decision_fingerprint"] != decision_fingerprint:
                        raise CandidateConflictError(
                            "Idempotency key was already used for another decision"
                        )
                    return CandidateDecisionResponse.model_validate(replay["response_json"])
                batch = connection.execute(
                    """
                    SELECT status, expires_at FROM cloud_ai_chunk_batches
                    WHERE owner_user_id=%s AND batch_id=%s FOR UPDATE
                    """,
                    (owner, batch_id),
                ).fetchone()
                if batch is None:
                    raise CandidateNotFoundError(batch_id)
                if batch["status"] == "expired" or batch["expires_at"] <= now:
                    self._expire_batch(connection, owner, batch_id)
                    connection.commit()
                    raise CandidateConflictError("Candidate batch has expired")
                row = connection.execute(
                    """
                    SELECT candidate.*, evidence.source_type, evidence.quote,
                        evidence.source_label, evidence.library_item_id_snapshot,
                        evidence.lesson_id, evidence.sentence_id, evidence.source_url,
                        evidence.sentence_translation, evidence.start_seconds,
                        evidence.end_seconds, evidence.evidence_start, evidence.evidence_end
                    FROM cloud_ai_chunk_candidates AS candidate
                    JOIN cloud_ai_chunk_evidence AS evidence
                      ON evidence.owner_user_id=candidate.owner_user_id
                     AND evidence.candidate_id=candidate.candidate_id
                    WHERE candidate.owner_user_id=%s AND candidate.batch_id=%s
                      AND candidate.candidate_id=%s FOR UPDATE OF candidate
                    """,
                    (owner, batch_id, candidate_id),
                ).fetchone()
                if row is None:
                    raise CandidateNotFoundError(candidate_id)
                candidate = self._row_to_candidate(row)
                if candidate.state != "proposed" or candidate.version != expected_version:
                    raise CandidateConflictError("Candidate decision is stale")
                if action != "reject":
                    self._validate_library_evidence(connection, owner, candidate)
                prepared = prepare(candidate)
                updated = prepared.candidate
                if (
                    updated.candidate_id != candidate.candidate_id
                    or updated.source_language != candidate.source_language
                    or updated.version != expected_version + 1
                    or updated.evidence.source_type != candidate.evidence.source_type
                    or updated.evidence.quote != candidate.evidence.quote
                    or updated.evidence.source_label != candidate.evidence.source_label
                    or updated.evidence.library_item_id != candidate.evidence.library_item_id
                    or updated.evidence.lesson_id != candidate.evidence.lesson_id
                    or updated.evidence.sentence_id != candidate.evidence.sentence_id
                    or updated.evidence.source_url != candidate.evidence.source_url
                    or updated.evidence.sentence_translation
                    != candidate.evidence.sentence_translation
                    or updated.evidence.start != candidate.evidence.start
                    or updated.evidence.end != candidate.evidence.end
                ):
                    raise ValueError("Candidate decision changed immutable source evidence")
                if action == "reject":
                    if prepared.study_input is not None or updated.state != "rejected":
                        raise ValueError("Rejected candidate cannot create a Study Bank item")
                elif prepared.study_input is None or updated.state not in {
                    "accepted",
                    "edited_and_accepted",
                }:
                    raise ValueError("Accepted candidate requires a Study Bank item")
                item = None
                created = False
                if prepared.study_input is not None:
                    item, created = self.study_bank.save_item_in_transaction(
                        connection, owner, prepared.study_input
                    )
                    updated = updated.model_copy(
                        update={
                            "accepted_study_item_id": item.id,
                            "existing_study_item_id": item.id,
                            "existing_study_item_active": item.active,
                            "normalized_identity": item.normalized_text,
                            "text": item.text,
                        }
                    )
                changed = connection.execute(
                    """
                    UPDATE cloud_ai_chunk_candidates SET
                        state=%s,version=%s,text=%s,normalized_identity=%s,
                        suggested_meaning_zh=%s,usage_note=%s,example=%s,
                        accepted_study_item_id=%s,existing_study_item_id=%s,
                        existing_study_item_active=%s
                    WHERE owner_user_id=%s AND batch_id=%s AND candidate_id=%s
                      AND state='proposed' AND version=%s
                    RETURNING candidate_id
                    """,
                    (
                        updated.state,
                        updated.version,
                        updated.text,
                        updated.normalized_identity,
                        updated.suggested_meaning_zh,
                        updated.usage_note,
                        updated.example,
                        updated.accepted_study_item_id,
                        updated.existing_study_item_id,
                        updated.existing_study_item_active,
                        owner,
                        batch_id,
                        candidate_id,
                        expected_version,
                    ),
                ).fetchone()
                if changed is None:
                    raise CandidateConflictError("Candidate decision is stale")
                connection.execute(
                    """
                    UPDATE cloud_ai_chunk_evidence SET evidence_start=%s,evidence_end=%s
                    WHERE owner_user_id=%s AND candidate_id=%s
                    """,
                    (
                        updated.evidence.evidence_start,
                        updated.evidence.evidence_end,
                        owner,
                        candidate_id,
                    ),
                )
                response = CandidateDecisionResponse(
                    candidate=updated, study_item=item, created=created, warnings=[]
                )
                decision_id = self.id_factory()
                connection.execute(
                    """
                    INSERT INTO cloud_ai_chunk_decisions (
                        owner_user_id,decision_id,batch_id,candidate_id,action,
                        candidate_version_before,candidate_version_after,
                        study_item_id,response_json,created_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        owner,
                        decision_id,
                        batch_id,
                        candidate_id,
                        action,
                        expected_version,
                        updated.version,
                        item.id if item else None,
                        Jsonb(response.model_dump(mode="json")),
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO cloud_ai_chunk_decision_keys (
                        owner_user_id,idempotency_key,decision_fingerprint,
                        decision_id,created_at,expires_at
                    ) VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        owner,
                        decision_key,
                        decision_fingerprint,
                        decision_id,
                        now,
                        now + timedelta(days=self.retention_days),
                    ),
                )
                return response
        except (
            CandidateConflictError,
            CandidateNotFoundError,
            CloudAiChunkOwnerNotProvisionedError,
            StudyBankConflictError,
            ValueError,
        ):
            raise
        except psycopg.errors.UniqueViolation as exc:
            raise CandidateConflictError("Candidate decision was submitted concurrently") from exc
        except psycopg.errors.ForeignKeyViolation as exc:
            raise CandidateConflictError(
                "Authorized source changed before the candidate decision committed"
            ) from exc
        except (psycopg.Error, CloudStudyBankUnavailableError) as exc:
            raise CloudAiChunkUnavailableError("Cloud candidate storage is unavailable") from exc
        except Exception as exc:
            raise CloudAiChunkUnavailableError("Cloud candidate storage is unavailable") from exc

    def expire_due(self, owner: str, *, limit: int = 100) -> int:
        """Bounded, owner-scoped retention maintenance; never runs implicitly."""
        if not 1 <= limit <= 1000:
            raise ValueError("Candidate cleanup limit is invalid")
        now = self._now()
        try:
            with self._connect() as connection:
                self._set_owner_context(connection, owner)
                rows = connection.execute(
                    """
                    SELECT batch_id FROM cloud_ai_chunk_batches
                    WHERE owner_user_id=%s AND status='ready' AND expires_at<=%s
                    ORDER BY expires_at,batch_id LIMIT %s FOR UPDATE SKIP LOCKED
                    """,
                    (owner, now, limit),
                ).fetchall()
                for row in rows:
                    self._expire_batch(connection, owner, str(row["batch_id"]))
                purge_rows = connection.execute(
                    """
                    SELECT batch_id FROM cloud_ai_chunk_batches
                    WHERE owner_user_id=%s AND status='expired' AND expires_at<=%s
                    ORDER BY expires_at,batch_id LIMIT %s FOR UPDATE SKIP LOCKED
                    """,
                    (owner, now - timedelta(days=self.retention_days), limit),
                ).fetchall()
                for row in purge_rows:
                    batch_id = str(row["batch_id"])
                    connection.execute(
                        """
                        DELETE FROM cloud_ai_chunk_decision_keys
                        WHERE owner_user_id=%s AND decision_id IN (
                            SELECT decision_id FROM cloud_ai_chunk_decisions
                            WHERE owner_user_id=%s AND batch_id=%s
                        )
                        """,
                        (owner, owner, batch_id),
                    )
                    connection.execute(
                        "DELETE FROM cloud_ai_chunk_decisions WHERE owner_user_id=%s AND batch_id=%s",
                        (owner, batch_id),
                    )
                    connection.execute(
                        "DELETE FROM cloud_ai_chunk_generation_requests WHERE owner_user_id=%s AND batch_id=%s",
                        (owner, batch_id),
                    )
                    connection.execute(
                        "DELETE FROM cloud_ai_chunk_batches WHERE owner_user_id=%s AND batch_id=%s",
                        (owner, batch_id),
                    )
                connection.execute(
                    """
                    DELETE FROM cloud_ai_chunk_decision_keys WHERE (owner_user_id,idempotency_key) IN (
                        SELECT owner_user_id,idempotency_key FROM cloud_ai_chunk_decision_keys
                        WHERE owner_user_id=%s AND expires_at<=%s
                        ORDER BY expires_at,idempotency_key LIMIT %s
                    )
                    """,
                    (owner, now, limit),
                )
                connection.execute(
                    """
                    DELETE FROM cloud_ai_chunk_generation_requests WHERE (owner_user_id,idempotency_key) IN (
                        SELECT owner_user_id,idempotency_key FROM cloud_ai_chunk_generation_requests
                        WHERE owner_user_id=%s AND expires_at<=%s
                        ORDER BY expires_at,idempotency_key LIMIT %s
                    )
                    """,
                    (owner, now, limit),
                )
                return len(rows) + len(purge_rows)
        except psycopg.Error as exc:
            raise CloudAiChunkUnavailableError("Cloud candidate storage is unavailable") from exc

    def _insert_candidate(self, connection, owner, batch_id, ordinal, candidate):
        connection.execute(
            """
            INSERT INTO cloud_ai_chunk_candidates (
                owner_user_id,batch_id,candidate_id,ordinal,state,version,
                source_language,text,normalized_identity,suggested_meaning_zh,
                usage_note,example,explanation_of_value,quality_flags,
                proposal_model,proposal_version,existing_study_item_id,
                existing_study_item_active,accepted_study_item_id
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                owner,
                batch_id,
                candidate.candidate_id,
                ordinal,
                candidate.state,
                candidate.version,
                candidate.source_language,
                candidate.text,
                candidate.normalized_identity,
                candidate.suggested_meaning_zh,
                candidate.usage_note,
                candidate.example,
                candidate.explanation_of_value,
                Jsonb(candidate.quality_flags),
                candidate.proposal_model,
                candidate.proposal_version,
                candidate.existing_study_item_id,
                candidate.existing_study_item_active,
                candidate.accepted_study_item_id,
            ),
        )
        evidence = candidate.evidence
        library_fk = (
            evidence.library_item_id
            if evidence.source_type == "library_sentence"
            else None
        )
        connection.execute(
            """
            INSERT INTO cloud_ai_chunk_evidence (
                owner_user_id,candidate_id,source_type,quote,source_label,
                library_item_id_snapshot,library_item_fk,lesson_id,sentence_id,
                source_url,sentence_translation,start_seconds,end_seconds,
                evidence_start,evidence_end
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                owner,
                candidate.candidate_id,
                evidence.source_type,
                evidence.quote,
                evidence.source_label,
                evidence.library_item_id,
                library_fk,
                evidence.lesson_id,
                evidence.sentence_id,
                evidence.source_url,
                evidence.sentence_translation,
                evidence.start,
                evidence.end,
                evidence.evidence_start,
                evidence.evidence_end,
            ),
        )

    @staticmethod
    def _validate_library_evidence(connection, owner: str, candidate: AiChunkCandidate) -> None:
        evidence = candidate.evidence
        if evidence.source_type == "manual_text":
            return
        row = connection.execute(
            """
            SELECT lesson_json FROM cloud_library_items
            WHERE owner_user_id=%s AND item_id=%s FOR KEY SHARE
            """,
            (owner, evidence.library_item_id),
        ).fetchone()
        if row is None:
            raise CandidateConflictError("Authorized Library source is no longer available")
        try:
            lesson = Lesson.model_validate(row["lesson_json"])
        except (ValueError, TypeError) as exc:
            raise CandidateConflictError("Authorized Library source is invalid") from exc
        sentence = next(
            (value for value in lesson.sentences if value.id == evidence.sentence_id),
            None,
        )
        expected = (
            lesson.id,
            lesson.title,
            lesson.source_url,
            lesson.source_language,
            lesson.target_language,
            sentence.text if sentence else None,
            sentence.translation if sentence else None,
            sentence.start if sentence else None,
            sentence.end if sentence else None,
        )
        actual = (
            evidence.lesson_id,
            evidence.source_label,
            evidence.source_url,
            candidate.source_language,
            "zh-CN",
            evidence.quote,
            evidence.sentence_translation,
            evidence.start,
            evidence.end,
        )
        if sentence is None or actual != expected:
            raise CandidateConflictError(
                "Candidate evidence no longer matches the authorized Library source"
            )
    def _load_batch(self, connection, owner: str, batch_id: str) -> AiChunkBatch:
        batch = connection.execute(
            "SELECT * FROM cloud_ai_chunk_batches WHERE owner_user_id=%s AND batch_id=%s",
            (owner, batch_id),
        ).fetchone()
        if batch is None:
            raise CandidateNotFoundError(batch_id)
        rows = connection.execute(
            """
            SELECT candidate.*, evidence.source_type, evidence.quote,
                evidence.source_label,evidence.library_item_id_snapshot,
                evidence.lesson_id,evidence.sentence_id,evidence.source_url,
                evidence.sentence_translation,evidence.start_seconds,
                evidence.end_seconds,evidence.evidence_start,evidence.evidence_end
            FROM cloud_ai_chunk_candidates AS candidate
            JOIN cloud_ai_chunk_evidence AS evidence
              ON evidence.owner_user_id=candidate.owner_user_id
             AND evidence.candidate_id=candidate.candidate_id
            WHERE candidate.owner_user_id=%s AND candidate.batch_id=%s
            ORDER BY candidate.ordinal
            """,
            (owner, batch_id),
        ).fetchall()
        return AiChunkBatch(
            batch_id=str(batch["batch_id"]),
            user_id=owner,
            source_language=batch["source_language"],
            source_type=batch["source_type"],
            source_label=batch["source_label"],
            created_at=batch["created_at"],
            expires_at=batch["expires_at"],
            status=batch["status"],
            candidates=[self._row_to_candidate(row) for row in rows],
        )

    @staticmethod
    def _row_to_candidate(row) -> AiChunkCandidate:
        evidence = CandidateEvidence(
            source_type=row["source_type"],
            quote=row["quote"],
            source_label=row["source_label"],
            library_item_id=row["library_item_id_snapshot"],
            lesson_id=row["lesson_id"],
            sentence_id=row["sentence_id"],
            source_url=row["source_url"],
            sentence_translation=row["sentence_translation"],
            start=row["start_seconds"],
            end=row["end_seconds"],
            evidence_start=row["evidence_start"],
            evidence_end=row["evidence_end"],
        )
        return AiChunkCandidate(
            candidate_id=str(row["candidate_id"]),
            state=row["state"],
            version=row["version"],
            source_language=row["source_language"],
            text=row["text"],
            normalized_identity=row["normalized_identity"],
            suggested_meaning_zh=row["suggested_meaning_zh"],
            usage_note=row["usage_note"],
            example=row["example"],
            explanation_of_value=row["explanation_of_value"],
            quality_flags=row["quality_flags"],
            evidence=evidence,
            proposal_model=row["proposal_model"],
            proposal_version=row["proposal_version"],
            existing_study_item_id=(
                str(row["existing_study_item_id"])
                if row["existing_study_item_id"] is not None
                else None
            ),
            existing_study_item_active=row["existing_study_item_active"],
            accepted_study_item_id=(
                str(row["accepted_study_item_id"])
                if row["accepted_study_item_id"] is not None
                else None
            ),
        )

    @staticmethod
    def _expire_batch(connection, owner: str, batch_id: str) -> None:
        connection.execute(
            "UPDATE cloud_ai_chunk_batches SET status='expired' WHERE owner_user_id=%s AND batch_id=%s",
            (owner, batch_id),
        )
        connection.execute(
            """
            UPDATE cloud_ai_chunk_candidates SET state='expired',version=version+1
            WHERE owner_user_id=%s AND batch_id=%s AND state='proposed'
            """,
            (owner, batch_id),
        )

    def _connect(self):
        return psycopg.connect(self._dsn, row_factory=dict_row)

    @staticmethod
    def _set_owner_context(connection, owner: str) -> None:
        connection.execute("SELECT set_config('app.current_owner', %s, true)", (owner,))

    @staticmethod
    def _assert_owner(connection, owner: str) -> None:
        if connection.execute(
            "SELECT 1 FROM cloud_users WHERE user_id=%s", (owner,)
        ).fetchone() is None:
            raise CloudAiChunkOwnerNotProvisionedError(
                "Cloud candidate owner is not provisioned"
            )

    @staticmethod
    def _validate_owner(owner: str) -> None:
        if not SAFE_OWNER_ID.fullmatch(owner):
            raise ValueError("Invalid candidate owner")

    @staticmethod
    def _validate_uuid(value: str) -> None:
        if not SAFE_UUID.fullmatch(value):
            raise CandidateNotFoundError(value)

    @staticmethod
    def _validate_key(value: str) -> None:
        if not SAFE_IDEMPOTENCY_KEY.fullmatch(value):
            raise ValueError("Invalid idempotency key")

    @staticmethod
    def _validate_fingerprint(value: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Invalid request fingerprint")

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Cloud candidate clock must be timezone-aware")
        return value.astimezone(timezone.utc)
