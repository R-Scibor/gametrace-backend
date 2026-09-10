from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.services.session_bounds import (
    SessionBoundsError,
    validate_manual_session_bounds,
)

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
PLUS2 = timezone(timedelta(hours=2))


def _ok(start, end, *, is_create=True, now=NOW):
    return validate_manual_session_bounds(
        start, end, now=now, is_create=is_create
    )


def test_exact_48h_passes():
    start, end = _ok(NOW - timedelta(hours=48), NOW)
    assert start.tzinfo is not None
    assert end - start == timedelta(hours=48)


def test_48h_plus_one_second_fails():
    with pytest.raises(SessionBoundsError) as ei:
        _ok(NOW - timedelta(hours=48, seconds=1), NOW)
    assert ei.value.detail == "session duration exceeds 48 hours"


def test_48h_plus_one_microsecond_fails():
    with pytest.raises(SessionBoundsError) as ei:
        _ok(NOW - timedelta(hours=48, microseconds=1), NOW)
    assert ei.value.detail == "session duration exceeds 48 hours"


def test_exact_30d_lookback_passes():
    start = NOW - timedelta(days=30)
    _ok(start, start + timedelta(hours=1))


def test_30d_plus_one_second_lookback_fails():
    start = NOW - timedelta(days=30, seconds=1)
    with pytest.raises(SessionBoundsError) as ei:
        _ok(start, start + timedelta(hours=1))
    assert ei.value.detail == "start_time is older than 30 days"


def test_exact_five_minute_future_passes():
    _ok(NOW + timedelta(minutes=4), NOW + timedelta(minutes=5))


def test_five_minute_plus_one_second_future_end_fails():
    with pytest.raises(SessionBoundsError) as ei:
        _ok(NOW, NOW + timedelta(minutes=5, seconds=1))
    assert ei.value.detail == "end_time cannot be in the future"


def test_five_minute_plus_one_second_future_start_fails():
    with pytest.raises(SessionBoundsError) as ei:
        _ok(NOW + timedelta(minutes=5, seconds=1), NOW + timedelta(minutes=6))
    assert ei.value.detail == "start_time cannot be in the future"


def test_end_equal_start_fails():
    with pytest.raises(SessionBoundsError) as ei:
        _ok(NOW, NOW)
    assert ei.value.detail == "end_time must be after start_time"


def test_end_before_start_does_not_fall_through_duration():
    with pytest.raises(SessionBoundsError) as ei:
        _ok(NOW, NOW - timedelta(hours=1))
    assert ei.value.detail == "end_time must be after start_time"


def test_naive_inputs_treated_as_utc():
    naive_start = datetime(2026, 9, 10, 10, 0, 0)  # no tz
    naive_end = datetime(2026, 9, 10, 11, 0, 0)
    start, end = _ok(naive_start, naive_end)
    assert start == datetime(2026, 9, 10, 10, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 9, 10, 11, 0, 0, tzinfo=UTC)


def test_aware_non_utc_converted():
    start = datetime(2026, 9, 10, 12, 0, 0, tzinfo=PLUS2)  # 10:00 UTC
    end = datetime(2026, 9, 10, 13, 0, 0, tzinfo=PLUS2)    # 11:00 UTC
    got_start, got_end = _ok(start, end)
    assert got_start == datetime(2026, 9, 10, 10, 0, 0, tzinfo=UTC)
    assert got_end == datetime(2026, 9, 10, 11, 0, 0, tzinfo=UTC)


def test_mixed_naive_and_aware():
    start = datetime(2026, 9, 10, 10, 0, 0)  # naive = UTC
    end = datetime(2026, 9, 10, 13, 0, 0, tzinfo=PLUS2)  # 11:00 UTC
    got_start, got_end = _ok(start, end)
    assert got_end - got_start == timedelta(hours=1)


def test_naive_now_is_normalized():
    naive_now = datetime(2026, 9, 10, 12, 0, 0)  # naive
    start, end = _ok(
        datetime(2026, 9, 10, 10, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 10, 11, 0, 0, tzinfo=UTC),
        now=naive_now,
    )
    assert (end - start) == timedelta(hours=1)


def test_is_create_false_skips_lookback():
    start = NOW - timedelta(days=40)
    end = start + timedelta(hours=2)
    _ok(start, end, is_create=False)


def test_is_create_false_skips_start_future_falls_through_to_end_future():
    # start beyond grace: create fails Rule 3. PATCH skips Rule 3, then
    # Rule 4 fires because end > start > now+grace. A PATCH "success" with
    # start in the future is impossible — end would also be in the future.
    start = NOW + timedelta(minutes=6)
    end = NOW + timedelta(minutes=7)
    with pytest.raises(SessionBoundsError) as ei_create:
        _ok(start, end, is_create=True)
    assert ei_create.value.detail == "start_time cannot be in the future"
    with pytest.raises(SessionBoundsError) as ei_patch:
        _ok(start, end, is_create=False)
    assert ei_patch.value.detail == "end_time cannot be in the future"


def test_is_create_false_still_rejects_duration():
    start = NOW - timedelta(days=40)
    with pytest.raises(SessionBoundsError) as ei:
        _ok(start, start + timedelta(hours=49), is_create=False)
    assert ei.value.detail == "session duration exceeds 48 hours"


def test_is_create_false_still_rejects_future_end():
    with pytest.raises(SessionBoundsError) as ei:
        _ok(NOW - timedelta(hours=1), NOW + timedelta(minutes=10), is_create=False)
    assert ei.value.detail == "end_time cannot be in the future"


def test_is_create_false_still_rejects_inverted():
    with pytest.raises(SessionBoundsError) as ei:
        _ok(NOW, NOW - timedelta(hours=1), is_create=False)
    assert ei.value.detail == "end_time must be after start_time"


def test_first_rule_wins_lookback_over_duration():
    start = NOW - timedelta(days=40)
    with pytest.raises(SessionBoundsError) as ei:
        _ok(start, start + timedelta(hours=400), is_create=True)
    assert ei.value.detail == "start_time is older than 30 days"


def test_first_rule_wins_duration_over_future_start():
    # start 10 min in the future, end 50h later — duration fires first
    with pytest.raises(SessionBoundsError) as ei:
        _ok(NOW + timedelta(minutes=10), NOW + timedelta(hours=50))
    assert ei.value.detail == "session duration exceeds 48 hours"


def test_monkeypatched_cap_still_emits_frozen_48_hours_string(monkeypatch):
    monkeypatch.setattr(settings, "session_max_duration_hours", 1)
    with pytest.raises(SessionBoundsError) as ei:
        _ok(NOW - timedelta(hours=2), NOW)
    assert ei.value.detail == "session duration exceeds 48 hours"
