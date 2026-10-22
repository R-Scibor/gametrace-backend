import time
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest

from app.core import bot_membership as bm

_OLD = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture
async def r():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


async def test_write_snapshot_replaces_and_stamps(r):
    await r.sadd(bm.VISIBLE_USERS_KEY, "old")
    await bm.write_snapshot(r, {"1001", "1002"}, complete=True, scan_started_at=int(time.time()))

    assert await r.smembers(bm.VISIBLE_USERS_KEY) == {"1001", "1002"}
    assert int(await r.get(bm.MEMBERSHIP_SYNCED_AT_KEY)) >= int(time.time()) - 2
    assert [k async for k in r.scan_iter(f"{bm.VISIBLE_USERS_KEY}:tmp:*")] == []


async def test_write_snapshot_is_one_transaction(r, monkeypatch):
    calls = []
    real = r.pipeline

    def spy(*a, **kw):
        calls.append(kw.get("transaction"))
        return real(*a, **kw)

    monkeypatch.setattr(r, "pipeline", spy)
    await bm.write_snapshot(r, {"1"}, complete=True, scan_started_at=int(time.time()))
    assert calls == [True]


async def test_write_snapshot_chunks_large_sets(r, monkeypatch):
    monkeypatch.setattr(bm, "_SADD_CHUNK", 2)
    await bm.write_snapshot(r, [str(i) for i in range(5)], complete=True, scan_started_at=int(time.time()))
    assert await r.scard(bm.VISIBLE_USERS_KEY) == 5


async def test_write_empty_snapshot_deletes_key(r):
    await r.sadd(bm.VISIBLE_USERS_KEY, "old")
    await bm.write_snapshot(r, set(), complete=True, scan_started_at=int(time.time()))

    assert await r.exists(bm.VISIBLE_USERS_KEY) == 0
    assert await r.get(bm.MEMBERSHIP_SYNCED_AT_KEY) is not None


async def test_add_and_remove(r):
    await bm.add_visible(r, "1")
    assert await r.sismember(bm.VISIBLE_USERS_KEY, "1")
    await bm.remove_visible(r, "1")
    assert not await r.sismember(bm.VISIBLE_USERS_KEY, "1")


async def test_synced_same_second_as_start_is_fresh(r):
    now = int(time.time())
    await r.set("bot:heartbeat", now)
    await r.set("bot:started_at", now)
    await r.set(bm.MEMBERSHIP_SYNCED_AT_KEY, now)
    await r.set(bm.MEMBERSHIP_COMPLETE_KEY, "1")
    await r.sadd(bm.VISIBLE_USERS_KEY, "5")

    assert await bm.get_bot_visibility(r, "5") is True
    assert await bm.get_bot_visibility(r, "6", _OLD) is False


async def _alive(r, *, complete="1"):
    now = int(time.time())
    await r.set("bot:heartbeat", now)
    await r.set("bot:started_at", now - 100)
    await r.set(bm.MEMBERSHIP_SYNCED_AT_KEY, now - 10)
    if complete is not None:
        await r.set(bm.MEMBERSHIP_COMPLETE_KEY, complete)


@pytest.mark.parametrize("complete", ["0", None])
async def test_partial_snapshot_never_proves_absence(r, complete):
    await _alive(r, complete=complete)
    await r.sadd(bm.VISIBLE_USERS_KEY, "5")
    assert await bm.get_bot_visibility(r, "5", _OLD) is True
    assert await bm.get_bot_visibility(r, "6", _OLD) is None


async def test_user_newer_than_scan_is_unknown(r):
    await _alive(r)
    just_now = datetime.now(UTC)
    within_margin = datetime.now(UTC) - timedelta(seconds=30)
    assert await bm.get_bot_visibility(r, "6", just_now) is None
    assert await bm.get_bot_visibility(r, "6", within_margin) is None
    assert await bm.get_bot_visibility(r, "6", None) is None
    assert await bm.get_bot_visibility(r, "6", _OLD) is False


async def test_write_snapshot_stores_metadata(r):
    await bm.write_snapshot(r, {"1"}, complete=False, scan_started_at=123)
    assert await r.get(bm.MEMBERSHIP_COMPLETE_KEY) == "0"
    assert await r.get(bm.MEMBERSHIP_SYNCED_AT_KEY) == "123"


async def test_naive_created_at_is_treated_as_utc(r):
    await _alive(r)
    naive_now = datetime.now(UTC).replace(tzinfo=None)
    assert await bm.get_bot_visibility(r, "6", naive_now) is None


async def test_coverage_margin_boundary(r):
    await _alive(r)
    synced = int(await r.get(bm.MEMBERSHIP_SYNCED_AT_KEY))
    edge = datetime.fromtimestamp(synced - bm.SCAN_COVERAGE_MARGIN_SECONDS, UTC)
    assert await bm.get_bot_visibility(r, "6", edge) is None
    assert await bm.get_bot_visibility(r, "6", edge - timedelta(seconds=1)) is False
