import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from services.retrieval.chunk_quality import prepare_chunk_text
from services.retrieval.chunk_store import ChunkStore
from services.schemas.lesson import SourceLanguage
from services.schemas.retrieval import (
    PracticeOutcome,
    StoredChunk,
)


DEFAULT_ACTIVE_RECALL_LIMIT = 5
MAX_ACTIVE_RECALL_LIMIT = 20
MASTERY_ADJUSTMENTS: dict[PracticeOutcome, float] = {
    "success": 0.10,
    "partial": 0.02,
    "failure": -0.10,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


class ChunkMemoryService:
    def __init__(
        self,
        store: ChunkStore,
        *,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] = _new_id,
    ):
        self.store = store
        self._clock = clock
        self._id_factory = id_factory

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def save_chunk(
        self,
        *,
        user_id: str,
        text: str,
        language: SourceLanguage,
        meaning: str = "",
        source_lesson_id: str | None = None,
        source_media_id: str | None = None,
        source_sentence_id: int | None = None,
        source_stage: str | None = None,
    ) -> tuple[StoredChunk, bool]:
        prepared = prepare_chunk_text(text, language)
        now = self._now()
        chunk = StoredChunk(
            id=self._id_factory(),
            user_id=user_id,
            text=prepared.text,
            normalized_text=prepared.normalized_text,
            meaning=meaning,
            source_language=language,
            source_lesson_id=source_lesson_id,
            source_media_id=source_media_id,
            source_sentence_id=source_sentence_id,
            source_stage=source_stage,
            created_at=now,
            updated_at=now,
        )
        return self.store.upsert_chunk(chunk)

    def list_chunks(
        self,
        user_id: str,
        language: SourceLanguage | None = None,
        *,
        include_archived: bool = False,
    ) -> list[StoredChunk]:
        chunks = [
            chunk
            for chunk in self.store.read_chunks()
            if chunk.user_id == user_id
            and (language is None or chunk.source_language == language)
            and (include_archived or chunk.active)
        ]
        return sorted(chunks, key=lambda chunk: (chunk.created_at, chunk.id))

    def record_practice(
        self,
        user_id: str,
        chunk_id: str,
        outcome: PracticeOutcome,
    ) -> StoredChunk:
        adjustment = MASTERY_ADJUSTMENTS[outcome]
        now = self._now()

        def apply(chunk: StoredChunk) -> StoredChunk:
            return chunk.model_copy(
                update={
                    "practice_count": chunk.practice_count + 1,
                    "success_count": chunk.success_count
                    + (1 if outcome == "success" else 0),
                    "failure_count": chunk.failure_count
                    + (1 if outcome == "failure" else 0),
                    "mastery_score": round(
                        max(0.0, min(1.0, chunk.mastery_score + adjustment)),
                        2,
                    ),
                    "last_practiced_at": now,
                    "updated_at": now,
                }
            )

        return self.store.update_chunk(user_id, chunk_id, apply)

    def set_active(
        self, user_id: str, chunk_id: str, active: bool
    ) -> StoredChunk:
        now = self._now()
        return self.store.update_chunk(
            user_id,
            chunk_id,
            lambda chunk: chunk.model_copy(
                update={"active": active, "updated_at": now}
            ),
        )

    def select_for_active_recall(
        self,
        user_id: str,
        language: SourceLanguage,
        *,
        current_lesson_id: str | None = None,
        limit: int = DEFAULT_ACTIVE_RECALL_LIMIT,
    ) -> list[StoredChunk]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        bounded_limit = max(1, min(limit, MAX_ACTIVE_RECALL_LIMIT))
        candidates = [
            chunk
            for chunk in self.list_chunks(user_id, language)
            if current_lesson_id is None
            or chunk.source_lesson_id != current_lesson_id
        ]

        def priority(chunk: StoredChunk):
            decided_attempts = chunk.success_count + chunk.failure_count
            success_rate = (
                chunk.success_count / decided_attempts
                if decided_attempts
                else 0.0
            )
            return (
                chunk.mastery_score,
                success_rate,
                -chunk.failure_count,
                chunk.last_practiced_at is not None,
                chunk.last_practiced_at or chunk.created_at,
                chunk.created_at,
                chunk.id,
            )

        return sorted(candidates, key=priority)[:bounded_limit]
