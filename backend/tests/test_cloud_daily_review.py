"""B-5: Cloud Daily Review — concurrent tabs and learner-local days.

Synthetic owners and a disposable PostgreSQL only."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.review.service import ReviewService, local_day_bounds, parse_time_zone
from services.schemas.study_bank import StudyItemPatch
from services.study_bank.postgres import PostgresOwnerScopedStudyBankRepository
from services.study_bank.store import StudyBankConflictError, StudyBankStore
from test_owner_scoped_study_bank import (  # noqa: F401  (fixture)
    OWNER_A,
    OWNER_B,
    _as_owner,
    _manual,
    _migration,
    cloud_repositories,
)


client = TestClient(app_module.app)
UTC = timezone.utc


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _cards(study, owner, count, prefix="card"):
    values = []
    for index in range(count):
        value = _manual(owner)
        values.append(value.model_copy(update={"text": f"{prefix} phrase {index}"}))
    return study.save_items_for_owner(owner, values)[0]


# --- concurrent tabs and retries (existing atomic rating, proven) -----------


def test_two_tabs_rating_one_card_have_exactly_one_winner(cloud_repositories):
    _, study, library = cloud_repositories
    [card] = _cards(study, OWNER_A, 1)
    barrier = threading.Barrier(2)

    def rate(rating):
        barrier.wait()
        return client.post(
            f"/api/review/items/{card.id}/rate",
            json={"user_id": OWNER_A, "rating": rating, "expected_review_count": 0},
        )

    with _as_owner(OWNER_A, study, library):
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(rate, ["good", "again"]))

    assert sorted(response.status_code for response in responses) == [200, 409]
    after = study.get_item(OWNER_A, card.id)
    assert after.review.review_count == 1
    winner = next(response for response in responses if response.status_code == 200)
    assert after.review.last_rating == winner.json()["item"]["review"]["last_rating"]


def test_a_retried_rating_never_counts_twice(cloud_repositories):
    _, study, library = cloud_repositories
    [card] = _cards(study, OWNER_A, 1)
    body = {"user_id": OWNER_A, "rating": "good", "expected_review_count": 0}
    with _as_owner(OWNER_A, study, library):
        first = client.post(f"/api/review/items/{card.id}/rate", json=body)
        retry = client.post(f"/api/review/items/{card.id}/rate", json=body)

    assert (first.status_code, retry.status_code) == (200, 409)
    assert study.get_item(OWNER_A, card.id).review.review_count == 1


def test_other_accounts_cannot_rate_or_queue_a_card(cloud_repositories):
    _, study, library = cloud_repositories
    [card] = _cards(study, OWNER_A, 1)
    with _as_owner(OWNER_B, study, library):
        rated = client.post(
            f"/api/review/items/{card.id}/rate",
            json={"user_id": OWNER_B, "rating": "good", "expected_review_count": 0},
        )
        forged = client.post(
            f"/api/review/items/{card.id}/rate",
            json={"user_id": OWNER_A, "rating": "good", "expected_review_count": 0},
        )
        queue = client.get("/api/review/queue", params={"user_id": OWNER_B, "time_zone": "Europe/Berlin"})

    assert rated.status_code == 404 and forged.status_code == 403
    assert queue.json()["items"] == []
    assert study.get_item(OWNER_A, card.id).review.review_count == 0


# --- first-review timestamps -----------------------------------------------


def test_only_the_first_rating_records_the_first_review(cloud_repositories):
    dsn, _, _ = cloud_repositories
    clock = Clock(datetime(2026, 11, 4, 9, 0, tzinfo=UTC))
    study = PostgresOwnerScopedStudyBankRepository(dsn, clock=clock)
    [card] = _cards(study, OWNER_A, 1)

    study.rate_item(OWNER_A, card.id, "good", 0)
    clock.value += timedelta(days=3)
    reviewed, _ = study.rate_item(OWNER_A, card.id, "good", 1)

    with psycopg.connect(dsn) as connection:
        first = connection.execute(
            "SELECT first_reviewed_at FROM cloud_study_items WHERE item_id = %s", (card.id,)
        ).fetchone()[0]
    assert first == datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
    assert reviewed.review.review_count == 2


# --- learner-local days ----------------------------------------------------


@pytest.mark.parametrize(
    "zone,instant,start,end,hours",
    [
        # US spring forward: 23-hour local day.
        ("America/New_York", "2026-03-08T12:00Z", "2026-03-08T05:00Z", "2026-03-09T04:00Z", 23),
        # US fall back: 25-hour local day.
        ("America/New_York", "2026-11-01T12:00Z", "2026-11-01T04:00Z", "2026-11-02T05:00Z", 25),
        ("Europe/Berlin", "2026-03-29T12:00Z", "2026-03-28T23:00Z", "2026-03-29T22:00Z", 23),
        ("Europe/Berlin", "2026-10-25T12:00Z", "2026-10-24T22:00Z", "2026-10-25T23:00Z", 25),
        # Chile skips local midnight: the day starts at the transition.
        ("America/Santiago", "2026-09-06T12:00Z", "2026-09-06T04:00Z", "2026-09-07T03:00Z", 23),
        ("Asia/Kolkata", "2026-11-04T20:00Z", "2026-11-04T18:30Z", "2026-11-05T18:30Z", 24),
        ("UTC", "2026-11-04T00:00Z", "2026-11-04T00:00Z", "2026-11-05T00:00Z", 24),
    ],
)
def test_local_day_bounds_follow_the_learners_calendar(zone, instant, start, end, hours):
    parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: E731
    lower, upper = local_day_bounds(parse(instant), ZoneInfo(zone))

    assert (lower, upper) == (parse(start), parse(end))
    assert upper - lower == timedelta(hours=hours)


@pytest.mark.parametrize("name", ["", "+08:00", "UTC+8", "CST", "../etc/passwd", "/etc/localtime", "Mars/Olympus"])
def test_only_iana_names_are_accepted(name):
    with pytest.raises(ValueError):
        parse_time_zone(name)


def test_a_first_review_belongs_to_the_local_day_it_happened_on_across_dst(cloud_repositories):
    """New York, 2026-03-08 (clocks jump 02:00 → 03:00): 23:59 local on the
    7th and 00:30 local on the 8th fall on different learner days."""
    dsn, _, _ = cloud_repositories
    zone = ZoneInfo("America/New_York")
    clock = Clock(datetime(2026, 3, 7, 23, 59, tzinfo=zone))
    study = PostgresOwnerScopedStudyBankRepository(dsn, clock=clock)
    late, early = _cards(study, OWNER_A, 2)
    study.rate_item(OWNER_A, late.id, "good", 0)
    clock.value = datetime(2026, 3, 8, 0, 30, tzinfo=zone)
    study.rate_item(OWNER_A, early.id, "good", 0)

    seventh = local_day_bounds(datetime(2026, 3, 7, 12, tzinfo=zone), zone)
    eighth = local_day_bounds(datetime(2026, 3, 8, 12, tzinfo=zone), zone)
    assert study.count_first_reviews(OWNER_A, *seventh) == 1
    assert study.count_first_reviews(OWNER_A, *eighth) == 1


# --- the daily new-card quota ---------------------------------------------


def test_new_cards_are_a_daily_quota_across_sessions_and_reset_next_local_day(cloud_repositories):
    dsn, _, _ = cloud_repositories
    zone = ZoneInfo("Europe/Berlin")
    clock = Clock(datetime(2026, 10, 25, 9, 0, tzinfo=zone))  # a 25-hour DST day
    study = PostgresOwnerScopedStudyBankRepository(dsn, clock=clock)
    service = ReviewService(study, clock=clock)
    cards = _cards(study, OWNER_A, 5)

    first_session = service.queue(OWNER_A, new_limit=2, time_zone="Europe/Berlin")
    for card in first_session:
        study.rate_item(OWNER_A, card.id, "good", 0)
    clock.value = datetime(2026, 10, 25, 23, 30, tzinfo=zone)  # same local day
    later_session = service.queue(OWNER_A, new_limit=2, time_zone="Europe/Berlin")
    clock.value = datetime(2026, 10, 26, 0, 30, tzinfo=zone)  # next local day
    next_day = service.queue(OWNER_A, new_limit=2, time_zone="Europe/Berlin")

    introduced = {card.id for card in first_session}
    tomorrow = {card.id for card in next_day if card.review.review_count == 0}
    assert len(introduced) == 2 and introduced <= {card.id for card in cards}
    assert [card for card in later_session if card.review.review_count == 0] == []
    assert len(tomorrow) == 2 and tomorrow.isdisjoint(introduced)


def test_the_quota_counts_only_the_learners_local_day_not_the_utc_day(cloud_repositories):
    """Tokyo is UTC+9: a card first reviewed at 08:00 local on 4 Nov was
    reviewed on 3 Nov in UTC, but it still uses up the 4 Nov local quota."""
    dsn, _, _ = cloud_repositories
    zone = ZoneInfo("Asia/Tokyo")
    clock = Clock(datetime(2026, 11, 4, 8, 0, tzinfo=zone))
    study = PostgresOwnerScopedStudyBankRepository(dsn, clock=clock)
    service = ReviewService(study, clock=clock)
    first, *_ = _cards(study, OWNER_A, 3)
    study.rate_item(OWNER_A, first.id, "good", 0)
    clock.value = datetime(2026, 11, 4, 22, 0, tzinfo=zone)

    tokyo = service.queue(OWNER_A, new_limit=1, time_zone="Asia/Tokyo")
    utc = service.queue(OWNER_A, new_limit=1, time_zone="UTC")

    assert [card for card in tokyo if card.review.review_count == 0] == []
    assert len([card for card in utc if card.review.review_count == 0]) == 1


def test_cards_reviewed_before_0012_never_count_against_today(cloud_repositories):
    dsn, _, _ = cloud_repositories
    clock = Clock(datetime(2026, 11, 4, 9, 0, tzinfo=UTC))
    study = PostgresOwnerScopedStudyBankRepository(dsn, clock=clock)
    old, *_ = _cards(study, OWNER_A, 3)
    study.rate_item(OWNER_A, old.id, "good", 0)
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE cloud_study_items SET first_reviewed_at = NULL")

    queue = ReviewService(study, clock=clock).queue(OWNER_A, new_limit=2, time_zone="UTC")

    assert len([card for card in queue if card.review.review_count == 0]) == 2


def test_queueing_never_changes_srs_state(cloud_repositories):
    dsn, _, _ = cloud_repositories
    clock = Clock(datetime(2026, 11, 4, 9, 0, tzinfo=UTC))
    study = PostgresOwnerScopedStudyBankRepository(dsn, clock=clock)
    cards = _cards(study, OWNER_A, 3)
    study.rate_item(OWNER_A, cards[0].id, "hard", 0)
    before = {card.id: (card.review, card.revision) for card in study.list_items(OWNER_A, active=None)}

    ReviewService(study, clock=clock).queue(OWNER_A, new_limit=1, time_zone="Pacific/Auckland")

    after = {card.id: (card.review, card.revision) for card in study.list_items(OWNER_A, active=None)}
    assert after == before


# --- API contract ----------------------------------------------------------


def test_the_queue_api_validates_and_applies_the_time_zone(cloud_repositories):
    _, study, library = cloud_repositories
    _cards(study, OWNER_A, 2)
    with _as_owner(OWNER_A, study, library):
        valid = client.get("/api/review/queue", params={"user_id": OWNER_A, "time_zone": "America/New_York", "new_limit": 1})
        invalid = client.get("/api/review/queue", params={"user_id": OWNER_A, "time_zone": "UTC+8"})
        without = client.get("/api/review/queue", params={"user_id": OWNER_A, "new_limit": 1})

    assert valid.status_code == 200 and len(valid.json()["items"]) == 1
    assert invalid.status_code == 422
    assert without.status_code == 200 and len(without.json()["items"]) == 1


def test_personal_desktop_keeps_a_per_session_new_limit(tmp_path):
    clock = Clock(datetime(2026, 11, 4, 9, 0, tzinfo=UTC))
    store = StudyBankStore(tmp_path / "study.json", clock=clock)
    store.save_items([_manual("local-learner").model_copy(update={"text": f"local phrase {i}"}) for i in range(3)])
    service = ReviewService(store, clock=clock)
    [first] = service.queue("local-learner", new_limit=1, time_zone="Europe/Berlin")
    store.rate_item("local-learner", first.id, "good", 0)

    again = service.queue("local-learner", new_limit=1, time_zone="Europe/Berlin")

    assert len([card for card in again if card.review.review_count == 0]) == 1


def test_archived_cards_cannot_be_rated_or_queued(cloud_repositories):
    _, study, _ = cloud_repositories
    [card] = _cards(study, OWNER_A, 1)
    study.update_item(OWNER_A, card.id, StudyItemPatch(active=False, expected_revision=card.revision))

    with pytest.raises(StudyBankConflictError):
        study.rate_item(OWNER_A, card.id, "good", 0)
    assert ReviewService(study).queue(OWNER_A, time_zone="UTC") == []


def test_0012_rolls_back_only_without_first_reviews(cloud_repositories):
    dsn, study, _ = cloud_repositories
    [card] = _cards(study, OWNER_A, 1)
    study.rate_item(OWNER_A, card.id, "good", 0)

    with pytest.raises(psycopg.errors.RaiseException):
        _migration(dsn, "0012_review_first_reviewed_at.down.sql")
    with psycopg.connect(dsn) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "UPDATE cloud_study_items SET review_count = 0, first_reviewed_at = now()"
            )
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE cloud_study_items SET first_reviewed_at = NULL")
    _migration(dsn, "0012_review_first_reviewed_at.down.sql")
    _migration(dsn, "0012_review_first_reviewed_at.up.sql")


def test_the_api_applies_the_daily_quota_with_a_time_zone(cloud_repositories):
    """The quota must reach the HTTP path, not only the service. Uses a real
    clock so the first review and the queue fall on the same local day."""
    dsn, _, library = cloud_repositories
    study = PostgresOwnerScopedStudyBankRepository(dsn)
    first, *_ = _cards(study, OWNER_A, 3)
    with _as_owner(OWNER_A, study, library):
        rated = client.post(
            f"/api/review/items/{first.id}/rate",
            json={"user_id": OWNER_A, "rating": "good", "expected_review_count": 0},
        )
        with_zone = client.get("/api/review/queue", params={"user_id": OWNER_A, "new_limit": 1, "time_zone": "UTC"})
        without = client.get("/api/review/queue", params={"user_id": OWNER_A, "new_limit": 1})

    new_with_zone = [card for card in with_zone.json()["items"] if card["review"]["review_count"] == 0]
    new_without = [card for card in without.json()["items"] if card["review"]["review_count"] == 0]
    assert rated.status_code == 200
    assert new_with_zone == []  # today's one new card is already used up
    assert len(new_without) == 1  # no zone: the per-session limit, as before
