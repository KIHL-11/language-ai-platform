from collections.abc import Callable
from datetime import datetime, timezone

from services.schemas.review import ReviewSummary
from services.schemas.study_bank import ReviewRating, StudyItem
from services.study_bank.owner_repository import OwnerScopedReviewRepository
from services.study_bank.store import utc_now


DEFAULT_SESSION_LIMIT = 20
DEFAULT_NEW_LIMIT = 10
MAX_SESSION_LIMIT = 100


class ReviewService:
    def __init__(
        self,
        store: OwnerScopedReviewRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.store = store
        self.clock = clock

    def summary(self, user_id: str, *, language: str | None = None) -> ReviewSummary:
        now = self._now()
        items = self.store.list_items(user_id, language=language, active=True)
        return ReviewSummary(
            due_words=sum(self._is_due(item, now) and item.kind == "word" for item in items),
            due_chunks=sum(self._is_due(item, now) and item.kind == "chunk" for item in items),
            new_words=sum(item.review.review_count == 0 and item.kind == "word" for item in items),
            new_chunks=sum(item.review.review_count == 0 and item.kind == "chunk" for item in items),
            active_words=sum(item.kind == "word" for item in items),
            active_chunks=sum(item.kind == "chunk" for item in items),
        )

    def queue(
        self,
        user_id: str,
        *,
        language: str | None = None,
        kind: str | None = None,
        limit: int = DEFAULT_SESSION_LIMIT,
        new_limit: int = DEFAULT_NEW_LIMIT,
    ) -> list[StudyItem]:
        if not 1 <= limit <= MAX_SESSION_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SESSION_LIMIT}")
        if not 0 <= new_limit <= MAX_SESSION_LIMIT:
            raise ValueError(f"new_limit must be between 0 and {MAX_SESSION_LIMIT}")
        now = self._now()
        items = self.store.list_items(
            user_id,
            kind=kind,
            language=language,
            active=True,
        )
        due = sorted(
            (item for item in items if self._is_due(item, now)),
            key=lambda item: (item.review.due_at, item.created_at, item.id),
        )
        selected = due[:limit]
        remaining = limit - len(selected)
        if remaining <= 0:
            return selected
        new = sorted(
            (item for item in items if item.review.review_count == 0),
            key=lambda item: (item.created_at, item.id),
        )
        return selected + new[:min(remaining, new_limit)]

    def rate(
        self,
        user_id: str,
        item_id: str,
        rating: ReviewRating,
        expected_review_count: int,
    ) -> tuple[StudyItem, datetime]:
        return self.store.rate_item(
            user_id,
            item_id,
            rating,
            expected_review_count,
        )

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Review clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _is_due(item: StudyItem, now: datetime) -> bool:
        return item.review.review_count > 0 and item.review.due_at <= now
