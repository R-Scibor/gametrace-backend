"""
tests/services/test_voice_quota.py

Unit tests for the per-user voice quota (app/services/voice_quota.py).

The daily window is counted from the voice_usage table; the hourly window is a
Redis counter. Both are keyed on user_id, never on the bearer token — see
test_voice.py for the re-login bypass regression tests at the endpoint level.
"""
from datetime import timedelta
from unittest.mock import patch

import fakeredis.aioredis
import pytest
import redis.exceptions

from app.services import voice_quota
from tests.factories import dt, make_user, make_voice_usage


@pytest.fixture
async def redis_client():
    """Fresh in-memory Redis per test — no shared state between tests, and the
    hourly counters start empty (make_user reuses discord_ids)."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def patch_get_redis(monkeypatch, redis_client):
    monkeypatch.setattr("app.services.voice_quota.get_redis", lambda: redis_client)


async def test_under_both_limits_returns_none(db):
    user = await make_user(db, discord_id="900000000000000001", username="quota-under")

    assert await voice_quota.check_voice_quota(db, user.discord_id) is None


async def test_hourly_limit_blocks_after_limit_attempts(db):
    user = await make_user(db, discord_id="900000000000000002", username="quota-hourly")

    for _ in range(voice_quota.HOURLY_LIMIT):
        assert await voice_quota.check_voice_quota(db, user.discord_id) is None

    retry_after = await voice_quota.check_voice_quota(db, user.discord_id)
    assert retry_after is not None
    assert 0 < retry_after <= voice_quota.HOURLY_WINDOW_SECONDS


async def test_daily_limit_blocks_without_spending_hourly(db, redis_client):
    """Daily is checked first and is read-only: a daily-blocked call must not
    consume hourly budget, since it spends no money."""
    user = await make_user(db, discord_id="900000000000000003", username="quota-daily")
    for _ in range(voice_quota.DAILY_LIMIT):
        await make_voice_usage(db, user.discord_id, created_at=dt(hours_ago=2))

    retry_after = await voice_quota.check_voice_quota(db, user.discord_id)
    assert retry_after is not None
    # Oldest counted row is 2h old, so ~22h remain on the 24h window.
    assert 21 * 3600 < retry_after <= 22 * 3600 + 5

    assert await redis_client.get(voice_quota.hourly_key(user.discord_id)) is None


async def test_usage_older_than_daily_window_is_not_counted(db):
    user = await make_user(db, discord_id="900000000000000004", username="quota-window")
    for _ in range(voice_quota.DAILY_LIMIT):
        await make_voice_usage(db, user.discord_id, created_at=dt(hours_ago=25))

    assert await voice_quota.check_voice_quota(db, user.discord_id) is None


async def test_quota_is_per_user(db):
    """One user exhausting the daily cap does not affect another."""
    spender = await make_user(db, discord_id="900000000000000005", username="quota-spender")
    bystander = await make_user(db, discord_id="900000000000000006", username="quota-bystander")
    for _ in range(voice_quota.DAILY_LIMIT):
        await make_voice_usage(db, spender.discord_id, created_at=dt(hours_ago=1))

    assert await voice_quota.check_voice_quota(db, spender.discord_id) is not None
    assert await voice_quota.check_voice_quota(db, bystander.discord_id) is None


async def test_redis_failure_fails_open(db):
    """A Redis outage must not take voice down: the hourly check degrades to
    'no limit'. The daily DB cap still binds, so spend stays bounded."""
    user = await make_user(db, discord_id="900000000000000007", username="quota-redisdown")

    with patch("app.services.voice_quota.get_redis",
               side_effect=redis.exceptions.ConnectionError("redis down")):
        assert await voice_quota.check_voice_quota(db, user.discord_id) is None


async def test_daily_cap_still_binds_when_redis_is_down(db):
    user = await make_user(db, discord_id="900000000000000008", username="quota-redisdaily")
    for _ in range(voice_quota.DAILY_LIMIT):
        await make_voice_usage(db, user.discord_id, created_at=dt(hours_ago=1))

    with patch("app.services.voice_quota.get_redis",
               side_effect=redis.exceptions.ConnectionError("redis down")):
        assert await voice_quota.check_voice_quota(db, user.discord_id) is not None


async def test_retry_after_is_at_least_one_second(db):
    """A row about to fall out of the window still yields a usable Retry-After."""
    user = await make_user(db, discord_id="900000000000000009", username="quota-edge")
    for _ in range(voice_quota.DAILY_LIMIT):
        await make_voice_usage(
            db, user.discord_id, created_at=dt(hours_ago=0) - timedelta(hours=24, seconds=-1)
        )

    retry_after = await voice_quota.check_voice_quota(db, user.discord_id)
    assert retry_after is not None and retry_after >= 1
