from datetime import datetime, timedelta, timezone

from services.schemas.study_bank import (
    DEFAULT_EASE,
    MAX_EASE,
    MIN_EASE,
    ReviewRating,
    ReviewState,
)


MINUTE = 60
DAY = 86_400
MAX_INTERVAL_SECONDS = 365 * DAY


def schedule_review(
    previous: ReviewState,
    rating: ReviewRating,
    now: datetime,
) -> ReviewState:
    """Return the next immutable review state for one authoritative rating."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Review clock must be timezone-aware")
    now = now.astimezone(timezone.utc)
    is_new = previous.review_count == 0
    interval = previous.interval_seconds
    ease = previous.ease_factor
    repetition_count = previous.repetition_count
    lapse_count = previous.lapse_count

    if rating == "again":
        interval = 10 * MINUTE
        repetition_count = 0
        lapse_count += 1
        ease = max(MIN_EASE, ease - 0.20)
    elif rating == "hard":
        repetition_count += 1
        ease = max(MIN_EASE, ease - 0.15)
        interval = DAY if is_new or interval < DAY else max(DAY, round(interval * 1.20))
    elif rating == "good":
        repetition_count += 1
        if is_new or interval < DAY:
            interval = DAY
        elif interval <= DAY:
            interval = 3 * DAY
        else:
            interval = round(interval * ease)
    else:
        repetition_count += 1
        ease = min(MAX_EASE, ease + 0.15)
        interval = 4 * DAY if is_new or interval < 4 * DAY else round(interval * previous.ease_factor * 1.30)

    interval = min(interval, MAX_INTERVAL_SECONDS)
    counters = {
        "again_count": previous.again_count,
        "hard_count": previous.hard_count,
        "good_count": previous.good_count,
        "easy_count": previous.easy_count,
    }
    counters[f"{rating}_count"] += 1
    return ReviewState(
        due_at=now + timedelta(seconds=interval),
        interval_seconds=interval,
        ease_factor=ease,
        repetition_count=repetition_count,
        lapse_count=lapse_count,
        review_count=previous.review_count + 1,
        last_reviewed_at=now,
        last_rating=rating,
        **counters,
    )
