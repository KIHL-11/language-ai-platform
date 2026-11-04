"""B-5: Cloud Daily Review — two-tab safety and the account's daily hard quota
of new cards (owner decision 2026-11-04). Synthetic owners, disposable PG."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.review import scheduler
from services.review.local_day import local_day_bounds, parse_time_zone
from services.review.service import ReviewService
from services.schemas.study_bank import StudyItemPatch
from services.study_bank.postgres import (
    DailyNewCardQuotaExceededError,
    PostgresOwnerScopedStudyBankRepository,
)
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
LIMIT = 10


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def quota(cloud_repositories):
    dsn, _, library = cloud_repositories
    clock = Clock(datetime(2026, 11, 4, 9, 0, tzinfo=UTC))
    study = PostgresOwnerScopedStudyBankRepository(dsn, clock=clock)
    return dsn, clock, study, library


def _cards(study, owner, count, *, language="en", kind="chunk", prefix="card"):
    values = []
    for index in range(count):
        value = _manual(owner).model_copy(update={
            "text": f"{prefix} {language} {kind} {index}", "source_language": language, "kind": kind,
        })
        if kind == "word":
            value = value.model_copy(update={"lemma": f"{prefix}{language}{index}"})
        values.append(value)
    return study.save_items_for_owner(owner, values)[0]


def _rate_api(card, owner=OWNER_A, expected=0, rating="good"):
    return client.post(
        f"/api/review/items/{card.id}/rate",
        json={"user_id": owner, "rating": rating, "expected_review_count": expected},
    )


def _new(items):
    return [item for item in items if item.review.review_count == 0]


def _ledger(dsn):
    with psycopg.connect(dsn) as connection:
        return connection.execute(
            "SELECT owner_user_id, quota_day::text FROM cloud_review_new_card_ledger ORDER BY consumed_at"
        ).fetchall()


# --- 1-3: one server-side quota, shared, not raisable ---------------------


def test_the_quota_applies_without_any_time_zone_in_the_request(quota):
    _, clock, study, library = quota
    cards = _cards(study, OWNER_A, LIMIT + 2)
    for card in cards[:LIMIT]:
        study.rate_item(OWNER_A, card.id, "good", 0)
    with _as_owner(OWNER_A, study, library):
        queue = client.get("/api/review/queue", params={"user_id": OWNER_A}).json()["items"]

    assert [item for item in queue if item["review"]["review_count"] == 0] == []


def test_english_german_words_and_chunks_share_one_daily_quota(quota):
    _, _, study, _ = quota
    mixed = (
        _cards(study, OWNER_A, 3, language="en", kind="word")
        + _cards(study, OWNER_A, 3, language="de", kind="word")
        + _cards(study, OWNER_A, 2, language="en", kind="chunk")
        + _cards(study, OWNER_A, 2, language="de", kind="chunk")
    )
    extra = _cards(study, OWNER_A, 1, language="de", kind="word", prefix="extra")[0]
    for card in mixed:
        study.rate_item(OWNER_A, card.id, "good", 0)

    with pytest.raises(DailyNewCardQuotaExceededError):
        study.rate_item(OWNER_A, extra.id, "good", 0)
    for language in ("en", "de"):
        for kind in ("word", "chunk"):
            assert _new(ReviewService(study, clock=study.clock).queue(OWNER_A, language=language, kind=kind)) == []


def test_new_limit_can_narrow_but_never_raise_the_quota(quota):
    _, _, study, library = quota
    _cards(study, OWNER_A, 30)
    service = ReviewService(study, clock=study.clock)

    assert len(_new(service.queue(OWNER_A, limit=100, new_limit=100))) == LIMIT
    assert len(_new(service.queue(OWNER_A, limit=100, new_limit=3))) == 3
    with _as_owner(OWNER_A, study, library):
        raised = client.put("/api/review/settings", json={"user_id": OWNER_A, "time_zone": "UTC", "daily_new_limit": 100})
    assert raised.status_code == 422


# --- 4-5: hard enforcement inside the rating transaction ------------------


def test_concurrent_clients_rating_different_new_cards_never_exceed_the_quota(quota):
    _, _, study, library = quota
    cards = _cards(study, OWNER_A, 16)
    for card in cards[:6]:
        study.rate_item(OWNER_A, card.id, "good", 0)
    barrier = threading.Barrier(10)

    def rate(card):
        barrier.wait()
        return _rate_api(card).status_code

    with _as_owner(OWNER_A, study, library):
        with ThreadPoolExecutor(max_workers=10) as executor:
            codes = list(executor.map(rate, cards[6:16]))

    assert codes.count(200) == 4 and codes.count(429) == 6
    assert study.new_card_allowance(OWNER_A).used_today == LIMIT


def test_rating_directly_cannot_bypass_the_quota_and_has_a_stable_code(quota):
    _, _, study, library = quota
    cards = _cards(study, OWNER_A, LIMIT + 1)
    with _as_owner(OWNER_A, study, library):
        for card in cards[:LIMIT]:
            assert _rate_api(card).status_code == 200
        over = _rate_api(cards[LIMIT])

    assert over.status_code == 429
    assert over.json()["detail"]["code"] == "daily_new_card_quota_exhausted"
    assert study.get_item(OWNER_A, cards[LIMIT].id).review.review_count == 0


# --- 6: deleting, archiving and restoring never refunds -------------------


def test_deleting_archiving_or_restoring_cards_never_refunds_the_day(quota):
    dsn, _, study, _ = quota
    cards = _cards(study, OWNER_A, LIMIT)
    for card in cards:
        study.rate_item(OWNER_A, card.id, "good", 0)
    first = study.get_item(OWNER_A, cards[0].id)
    study.delete_item(OWNER_A, first.id, first.revision)
    second = study.get_item(OWNER_A, cards[1].id)
    archived = study.update_item(OWNER_A, second.id, StudyItemPatch(active=False, expected_revision=second.revision))
    study.update_item(OWNER_A, second.id, StudyItemPatch(active=True, expected_revision=archived.revision))
    replacement = _cards(study, OWNER_A, 1, prefix="readded")[0]

    with pytest.raises(DailyNewCardQuotaExceededError):
        study.rate_item(OWNER_A, replacement.id, "good", 0)
    assert len(_ledger(dsn)) == LIMIT
    assert study.new_card_allowance(OWNER_A).remaining == 0


# --- 7: time zone changes, midnight and DST -------------------------------


def test_settings_default_to_utc_and_a_first_choice_applies_at_once(quota):
    _, _, study, library = quota
    with _as_owner(OWNER_A, study, library):
        before = client.get("/api/review/settings", params={"user_id": OWNER_A}).json()
        chosen = client.put("/api/review/settings", json={"user_id": OWNER_A, "time_zone": "Europe/Berlin"}).json()
        invalid = client.put("/api/review/settings", json={"user_id": OWNER_A, "time_zone": "UTC+8"})

    assert (before["time_zone"], before["configured"], before["daily_new_limit"]) == ("UTC", False, LIMIT)
    assert (chosen["time_zone"], chosen["configured"], chosen["pending_time_zone"]) == ("Europe/Berlin", True, None)
    assert invalid.status_code == 422


def test_a_time_zone_change_after_spending_waits_for_the_next_local_midnight(quota):
    _, clock, study, _ = quota
    tokyo = ZoneInfo("Asia/Tokyo")
    study.set_time_zone(OWNER_A, "Asia/Tokyo")
    clock.value = datetime(2026, 11, 4, 20, 0, tzinfo=tokyo)
    cards = _cards(study, OWNER_A, LIMIT + 1)
    for card in cards[:LIMIT]:
        study.rate_item(OWNER_A, card.id, "good", 0)

    # Switching to New York (where it is still the morning of 4 Nov) must not
    # open a fresh 4 Nov window.
    pending = study.set_time_zone(OWNER_A, "America/New_York")
    with pytest.raises(DailyNewCardQuotaExceededError):
        study.rate_item(OWNER_A, cards[LIMIT].id, "good", 0)
    clock.value = datetime(2026, 11, 5, 0, 1, tzinfo=tokyo)  # Tokyo midnight: switch applies
    after = study.review_settings(OWNER_A)
    allowance = study.new_card_allowance(OWNER_A)

    assert (pending.time_zone, pending.pending_time_zone) == ("Asia/Tokyo", "America/New_York")
    assert pending.pending_effective_at == datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
    assert (after.time_zone, after.pending_time_zone) == ("America/New_York", None)
    # New York's date is now 4 Nov: the same calendar day already spent.
    assert (allowance.quota_day.isoformat(), allowance.remaining) == ("2026-11-04", 0)
    clock.value = datetime(2026, 11, 5, 0, 1, tzinfo=ZoneInfo("America/New_York"))
    assert study.new_card_allowance(OWNER_A).remaining == LIMIT


def test_a_switch_more_than_a_day_behind_cannot_reopen_an_earlier_date(quota):
    _, clock, study, _ = quota
    kiritimati = ZoneInfo("Pacific/Kiritimati")  # UTC+14
    study.set_time_zone(OWNER_A, "Pacific/Kiritimati")
    clock.value = datetime(2026, 11, 4, 22, 0, tzinfo=kiritimati)
    cards = _cards(study, OWNER_A, LIMIT + 1)
    for card in cards[:LIMIT]:
        study.rate_item(OWNER_A, card.id, "good", 0)
    study.set_time_zone(OWNER_A, "Pacific/Pago_Pago")  # UTC-11, 25 hours behind

    # Kiritimati midnight: in Pago Pago it is still 3 Nov, a date with no
    # ledger rows, yet the spent cards were all rated inside that local day.
    clock.value = datetime(2026, 11, 5, 0, 1, tzinfo=kiritimati)
    allowance = study.new_card_allowance(OWNER_A)
    with pytest.raises(DailyNewCardQuotaExceededError):
        study.rate_item(OWNER_A, cards[LIMIT].id, "good", 0)

    assert (allowance.time_zone, allowance.quota_day.isoformat()) == ("Pacific/Pago_Pago", "2026-11-03")
    assert allowance.remaining == 0
    # 4 Nov in Pago Pago shares its date with the spent Kiritimati day, so it
    # stays spent too (conservative); 5 Nov is a fresh day.
    clock.value = datetime(2026, 11, 4, 0, 1, tzinfo=ZoneInfo("Pacific/Pago_Pago"))
    assert study.new_card_allowance(OWNER_A).remaining == 0
    clock.value = datetime(2026, 11, 5, 0, 1, tzinfo=ZoneInfo("Pacific/Pago_Pago"))
    assert study.new_card_allowance(OWNER_A).remaining == LIMIT


def test_the_quota_window_spans_a_whole_25_hour_dst_day(quota):
    _, clock, study, _ = quota
    berlin = ZoneInfo("Europe/Berlin")
    clock.value = datetime(2026, 10, 25, 0, 30, tzinfo=berlin)  # DST ends at 03:00 that day
    study.set_time_zone(OWNER_A, "Europe/Berlin")
    cards = _cards(study, OWNER_A, LIMIT + 1)
    for card in cards[:LIMIT]:
        study.rate_item(OWNER_A, card.id, "good", 0)
    clock.value = datetime(2026, 10, 25, 23, 50, tzinfo=berlin)  # 24h20 later, same local day

    with pytest.raises(DailyNewCardQuotaExceededError):
        study.rate_item(OWNER_A, cards[LIMIT].id, "good", 0)
    clock.value = datetime(2026, 10, 26, 0, 5, tzinfo=berlin)
    study.rate_item(OWNER_A, cards[LIMIT].id, "good", 0)


@pytest.mark.parametrize(
    "zone,instant,start,end,hours",
    [
        ("America/New_York", "2026-03-08T12:00Z", "2026-03-08T05:00Z", "2026-03-09T04:00Z", 23),
        ("America/New_York", "2026-11-01T12:00Z", "2026-11-01T04:00Z", "2026-11-02T05:00Z", 25),
        ("Europe/Berlin", "2026-03-29T12:00Z", "2026-03-28T23:00Z", "2026-03-29T22:00Z", 23),
        ("Europe/Berlin", "2026-10-25T12:00Z", "2026-10-24T22:00Z", "2026-10-25T23:00Z", 25),
        ("America/Santiago", "2026-09-06T12:00Z", "2026-09-06T04:00Z", "2026-09-07T03:00Z", 23),
        ("Asia/Kolkata", "2026-11-04T20:00Z", "2026-11-04T18:30Z", "2026-11-05T18:30Z", 24),
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


# --- 8: retries, lost responses and failed transactions -------------------


def test_a_retry_or_lost_response_never_charges_twice(quota):
    dsn, _, study, library = quota
    [card] = _cards(study, OWNER_A, 1)
    with _as_owner(OWNER_A, study, library):
        first = _rate_api(card)
        retry = _rate_api(card)

    assert (first.status_code, retry.status_code) == (200, 409)
    assert len(_ledger(dsn)) == 1
    assert study.get_item(OWNER_A, card.id).review.review_count == 1


def test_a_failed_rating_transaction_rolls_back_its_quota_charge(quota, monkeypatch):
    dsn, _, study, _ = quota
    [card] = _cards(study, OWNER_A, 1)

    def broken(*_args, **_kwargs):
        raise RuntimeError("scheduler failure after the quota charge")

    import services.study_bank.postgres as postgres_module
    monkeypatch.setattr(postgres_module, "schedule_review", broken)
    with pytest.raises(RuntimeError):
        study.rate_item(OWNER_A, card.id, "good", 0)
    monkeypatch.setattr(postgres_module, "schedule_review", scheduler.schedule_review)

    assert _ledger(dsn) == []
    assert study.get_item(OWNER_A, card.id).review.review_count == 0
    study.rate_item(OWNER_A, card.id, "good", 0)
    assert len(_ledger(dsn)) == 1


def test_two_tabs_rating_one_card_have_exactly_one_winner(quota):
    dsn, _, study, library = quota
    [card] = _cards(study, OWNER_A, 1)
    barrier = threading.Barrier(2)

    def rate(rating):
        barrier.wait()
        return _rate_api(card, rating=rating).status_code

    with _as_owner(OWNER_A, study, library):
        with ThreadPoolExecutor(max_workers=2) as executor:
            codes = list(executor.map(rate, ["good", "again"]))

    assert sorted(codes) == [200, 409]
    assert len(_ledger(dsn)) == 1


# --- 9: due cards are never limited ---------------------------------------


def test_due_reviewed_cards_still_rate_when_the_quota_is_spent(quota):
    _, clock, study, library = quota
    cards = _cards(study, OWNER_A, LIMIT)
    for card in cards:
        study.rate_item(OWNER_A, card.id, "again", 0)
    clock.value += timedelta(minutes=30)  # "again" cards are due again soon
    due = ReviewService(study, clock=clock).queue(OWNER_A)
    with _as_owner(OWNER_A, study, library):
        rated = _rate_api(due[0], expected=1)

    assert len(due) == LIMIT and all(card.review.review_count == 1 for card in due)
    assert rated.status_code == 200
    assert study.new_card_allowance(OWNER_A).used_today == LIMIT


# --- 10: isolation, Personal, old data and migration safety ---------------


def test_one_owners_spending_never_touches_anothers_quota(quota):
    _, _, study, library = quota
    for card in _cards(study, OWNER_A, LIMIT):
        study.rate_item(OWNER_A, card.id, "good", 0)
    with _as_owner(OWNER_B, study, library):
        settings = client.get("/api/review/settings", params={"user_id": OWNER_B}).json()
        forged = client.get("/api/review/settings", params={"user_id": OWNER_A})

    assert settings["new_cards_today"] == 0 and settings["new_cards_remaining"] == LIMIT
    assert forged.status_code == 403


def test_cards_reviewed_before_the_quota_existed_are_not_charged(quota):
    dsn, _, study, _ = quota
    [old] = _cards(study, OWNER_A, 1)
    study.rate_item(OWNER_A, old.id, "good", 0)
    # Simulate a card reviewed before 0012 existed: no ledger row for it.
    with psycopg.connect(dsn) as connection:
        connection.execute("DELETE FROM cloud_review_new_card_ledger")
    before = study.get_item(OWNER_A, old.id).review

    study.rate_item(OWNER_A, old.id, "good", 1)

    assert _ledger(dsn) == []
    assert study.get_item(OWNER_A, old.id).review.review_count == before.review_count + 1


def test_personal_desktop_keeps_its_per_session_limit_and_has_no_settings(tmp_path):
    clock = Clock(datetime(2026, 11, 4, 9, 0, tzinfo=UTC))
    store = StudyBankStore(tmp_path / "study.json", clock=clock)
    store.save_items([_manual("local-learner").model_copy(update={"text": f"local {i}"}) for i in range(15)])
    service = ReviewService(store, clock=clock)
    for card in service.queue("local-learner", new_limit=12):
        store.rate_item("local-learner", card.id, "good", 0)

    assert len(_new(service.queue("local-learner", new_limit=3))) == 3
    response = client.get("/api/review/settings", params={"user_id": "local-learner"})
    assert response.status_code == 404


def test_archived_cards_still_cannot_be_rated(quota):
    _, _, study, _ = quota
    [card] = _cards(study, OWNER_A, 1)
    study.update_item(OWNER_A, card.id, StudyItemPatch(active=False, expected_revision=card.revision))
    with pytest.raises(StudyBankConflictError):
        study.rate_item(OWNER_A, card.id, "good", 0)


def test_0012_rollback_refuses_while_quota_data_exists(quota):
    dsn, _, study, _ = quota
    [card] = _cards(study, OWNER_A, 1)
    study.rate_item(OWNER_A, card.id, "good", 0)

    with pytest.raises(psycopg.errors.RaiseException):
        _migration(dsn, "0012_review_new_card_quota.down.sql")
    assert len(_ledger(dsn)) == 1
