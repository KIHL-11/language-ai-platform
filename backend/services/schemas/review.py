from datetime import datetime
from typing import Literal

from pydantic import Field

from services.schemas.study_bank import (
    ReviewRating,
    StudyItem,
    StudyModel,
)


class ReviewSummary(StudyModel):
    due_words: int
    due_chunks: int
    new_words: int
    new_chunks: int
    active_words: int
    active_chunks: int


class ReviewQueue(StudyModel):
    items: list[StudyItem]


class ReviewRatingRequest(StudyModel):
    user_id: str = Field(min_length=1, max_length=128)
    rating: ReviewRating
    expected_review_count: int = Field(ge=0)


class ReviewRatingResponse(StudyModel):
    item: StudyItem
    previous_due_at: datetime
    next_due_at: datetime
    interval_seconds: int


ReviewKind = Literal["word", "chunk"]


class ReviewSettings(StudyModel):
    """Cloud Daily Review settings. The daily limit is server-side only."""

    time_zone: str
    configured: bool
    daily_new_limit: int
    new_cards_today: int
    new_cards_remaining: int
    quota_day: str
    pending_time_zone: str | None = None
    pending_effective_at: datetime | None = None


class ReviewSettingsUpdate(StudyModel):
    user_id: str = Field(min_length=1, max_length=128)
    time_zone: str = Field(min_length=1, max_length=64)
