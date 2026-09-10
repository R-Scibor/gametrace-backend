from datetime import UTC, datetime, timedelta

from app.core.config import settings

# Frozen public contract — do not interpolate settings into these.
DETAIL_END_BEFORE_START = "end_time must be after start_time"
DETAIL_LOOKBACK = "start_time is older than 30 days"
DETAIL_DURATION = "session duration exceeds 48 hours"
DETAIL_FUTURE_START = "start_time cannot be in the future"
DETAIL_FUTURE_END = "end_time cannot be in the future"


class SessionBoundsError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def validate_manual_session_bounds(
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
    is_create: bool,
) -> tuple[datetime, datetime]:
    """Normalize to UTC, enforce rules, return (start_utc, end_utc)."""
    now_utc = _as_utc(now if now is not None else datetime.now(UTC))
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)

    if end_utc <= start_utc:
        raise SessionBoundsError(DETAIL_END_BEFORE_START)

    lookback = timedelta(days=settings.session_max_lookback_days)
    max_duration = timedelta(hours=settings.session_max_duration_hours)
    grace = timedelta(minutes=settings.session_future_grace_minutes)

    if is_create and start_utc < now_utc - lookback:
        raise SessionBoundsError(DETAIL_LOOKBACK)
    if end_utc - start_utc > max_duration:
        raise SessionBoundsError(DETAIL_DURATION)
    if is_create and start_utc > now_utc + grace:
        raise SessionBoundsError(DETAIL_FUTURE_START)
    if end_utc > now_utc + grace:
        raise SessionBoundsError(DETAIL_FUTURE_END)

    return start_utc, end_utc
