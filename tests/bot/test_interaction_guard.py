import fakeredis.aioredis
import pytest

from app.bot.interaction_guard import (
    MAX_IN_FLIGHT_FOLLOWUPS,
    Action,
    claim_cooldown,
    cooldown_remaining,
    release_followup,
    reset_followups_for_tests,
    try_acquire_followup,
)


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def _reset():
    reset_followups_for_tests()
    yield
    reset_followups_for_tests()


async def test_first_code_claim_arms_thirty_seconds(redis):
    assert await claim_cooldown(redis, "9", Action.CODE) is None
    ttl = await redis.ttl("bot:cd:code:9")
    assert 1 <= ttl <= 30
    remaining = await cooldown_remaining(redis, "9", Action.CODE)
    assert 1 <= remaining <= 30


async def test_second_claim_does_not_refresh_the_ttl(redis):
    await claim_cooldown(redis, "9", Action.CODE)
    await redis.set("bot:cd:code:9", "1", ex=10)
    again = await claim_cooldown(redis, "9", Action.CODE)
    assert 1 <= again <= 10
    assert await redis.ttl("bot:cd:code:9") <= 10


async def test_stats_and_code_keys_are_independent(redis):
    await claim_cooldown(redis, "9", Action.CODE)
    assert await claim_cooldown(redis, "9", Action.STATS) is None


async def test_redis_error_fails_open(redis, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("redis down")
    monkeypatch.setattr(redis, "set", boom)
    monkeypatch.setattr(redis, "ttl", boom)
    assert await cooldown_remaining(redis, "9", Action.CODE) is None
    assert await claim_cooldown(redis, "9", Action.CODE) is None


def test_cap_allows_eight_and_a_release_frees_one():
    assert all(try_acquire_followup() for _ in range(MAX_IN_FLIGHT_FOLLOWUPS))
    assert try_acquire_followup() is False
    release_followup()
    assert try_acquire_followup() is True


def test_lost_claim_release_is_once():
    assert try_acquire_followup() is True
    release_followup()
    with pytest.raises(RuntimeError):
        release_followup()


async def test_short_actions_use_five_second_windows(redis):
    for action, key in (
        (Action.STATS, "bot:cd:stats:9"),
        (Action.RECENT, "bot:cd:recent:9"),
        (Action.LOGOUT, "bot:cd:logout:9"),
    ):
        assert await claim_cooldown(redis, "9", action) is None
        ttl = await redis.ttl(key)
        assert 1 <= ttl <= 5
        remaining = await cooldown_remaining(redis, "9", action)
        assert 1 <= remaining <= 5
