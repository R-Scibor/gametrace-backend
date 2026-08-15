"""
tests/services/test_rate_limit.py

The shared per-user hourly quota helper (app/core/rate_limit.py).
Endpoint-level coverage lives in test_voice.py, test_games_match.py and
test_games_create.py; this file covers the counter mechanics.
"""
from unittest.mock import patch

import fakeredis.aioredis
import pytest
import redis.exceptions

from app.core import rate_limit


@pytest.fixture
async def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def patch_get_redis(monkeypatch, redis_client):
    monkeypatch.setattr("app.core.rate_limit.get_redis", lambda: redis_client)


async def test_allows_up_to_limit_then_blocks():
    for _ in range(3):
        assert await rate_limit.check_hourly_quota("u1", "b", 3) is None

    retry_after = await rate_limit.check_hourly_quota("u1", "b", 3)
    assert retry_after is not None
    assert 0 < retry_after <= rate_limit.HOURLY_WINDOW_SECONDS


async def test_buckets_are_independent():
    for _ in range(4):
        await rate_limit.check_hourly_quota("u1", "voice", 3)

    assert await rate_limit.check_hourly_quota("u1", "games_match", 3) is None


async def test_first_call_arms_the_ttl(redis_client):
    await rate_limit.check_hourly_quota("u1", "b", 3)

    assert 0 < await redis_client.ttl(rate_limit.hourly_key("b", "u1")) <= 3600


async def test_key_without_ttl_is_rearmed(redis_client):
    """A crash or connection blip between INCR and EXPIRE used to leave a key
    with no TTL. Nothing re-armed it, so once the counter passed the limit the
    user was 429'd forever with Retry-After: 1 — a client backoff loop that
    never recovers. The counter must repair itself instead."""
    key = rate_limit.hourly_key("b", "u1")
    await redis_client.set(key, "99")  # no TTL, already over any limit
    assert await redis_client.ttl(key) == -1

    retry_after = await rate_limit.check_hourly_quota("u1", "b", 3)

    assert retry_after is not None and retry_after > 1
    assert 0 < await redis_client.ttl(key) <= 3600


async def test_existing_ttl_is_not_extended(redis_client):
    """Re-arming must not slide the window forward on every request, or a
    steady caller would never see the hour roll over."""
    key = rate_limit.hourly_key("b", "u1")
    await rate_limit.check_hourly_quota("u1", "b", 3)
    await redis_client.expire(key, 42)

    await rate_limit.check_hourly_quota("u1", "b", 3)

    assert 0 < await redis_client.ttl(key) <= 42


async def test_redis_failure_fails_open():
    with patch("app.core.rate_limit.get_redis",
               side_effect=redis.exceptions.ConnectionError("down")):
        assert await rate_limit.check_hourly_quota("u1", "b", 1) is None
