"""Learner-local calendar days (B-5): IANA time zones and DST-correct day
bounds. No dependencies, so both the review service and the PostgreSQL Study
Bank can use it."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_time_zone(name: str) -> ZoneInfo:
    """A learner's IANA time zone (for example "Europe/Berlin"); ValueError
    for anything else, including offsets and abbreviations."""
    if not name or len(name) > 64 or name.startswith(("/", ".")) or ".." in name:
        raise ValueError("time_zone must be an IANA time zone name")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("time_zone must be an IANA time zone name") from exc


def local_day_bounds(now: datetime, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """The learner-local calendar day containing ``now``, as a UTC half-open
    interval. Each midnight is resolved on its own, so a day is 23 or 25
    hours across a DST change. Where a transition skips local midnight (for
    example America/Santiago), zoneinfo resolves the missing 00:00 with the
    pre-transition offset, which is exactly the transition instant, so the
    day starts at the first local time that exists."""
    local_date = now.astimezone(zone).date()

    def start_of(day) -> datetime:
        return datetime.combine(day, time(0), tzinfo=zone).astimezone(timezone.utc)

    return start_of(local_date), start_of(local_date + timedelta(days=1))
