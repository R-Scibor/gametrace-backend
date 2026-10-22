"""Bot-side maintenance of the Redis visibility snapshot."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

from app.bot import membership
from app.core.bot_membership import MEMBERSHIP_COMPLETE_KEY, VISIBLE_USERS_KEY
from app.services.demo import DEMO_DISCORD_ID
from tests.factories import make_user

A, B, C = "100000000000000001", "100000000000000002", "100000000000000003"


def _guild(gid, member_ids, *, chunked=True, unavailable=False):
    ids = {int(m) for m in member_ids}
    return SimpleNamespace(
        id=gid,
        chunked=chunked,
        unavailable=unavailable,
        get_member=lambda uid: object() if uid in ids else None,
    )


def _member(uid, guild):
    return SimpleNamespace(id=int(uid), guild=guild)


@pytest.fixture
async def r():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


async def test_rebuild_stores_only_visible_registered_users(db, r):
    for uid in (A, B, C):
        await make_user(db, discord_id=uid, username=uid)
    await make_user(db, discord_id=DEMO_DISCORD_ID, username="demo")
    guilds = [_guild(1, [A, "999"]), _guild(2, [B, DEMO_DISCORD_ID])]

    count = await membership.rebuild_snapshot(db, r, guilds)

    assert count == 2
    assert await r.smembers(VISIBLE_USERS_KEY) == {A, B}


async def test_member_join_registered_added(db, r):
    await make_user(db, discord_id=A, username="a")
    g = _guild(1, [A])
    await membership.on_member_join(db, r, _member(A, g))
    assert await r.sismember(VISIBLE_USERS_KEY, A)


async def test_member_join_unregistered_ignored(db, r):
    g = _guild(1, [B])
    await membership.on_member_join(db, r, _member(B, g))
    assert await r.scard(VISIBLE_USERS_KEY) == 0


async def test_member_remove_kept_when_in_other_guild(r):
    await r.sadd(VISIBLE_USERS_KEY, A)
    left, other = _guild(1, [A]), _guild(2, [A])

    await membership.on_member_remove(r, [left, other], _member(A, left))
    assert await r.sismember(VISIBLE_USERS_KEY, A)


async def test_member_remove_dropped_when_no_other_guild(r):
    await r.sadd(VISIBLE_USERS_KEY, A)
    left = _guild(1, [A])  # discord.py may still return the member in the cache

    await membership.on_member_remove(r, [left, _guild(2, [])], _member(A, left))
    assert not await r.sismember(VISIBLE_USERS_KEY, A)


async def test_member_remove_unregistered_is_noop(r):
    await r.sadd(VISIBLE_USERS_KEY, A)
    g = _guild(1, [])
    await membership.on_member_remove(r, [g], _member(B, g))
    assert await r.smembers(VISIBLE_USERS_KEY) == {A}


async def test_rebuild_drops_users_that_left_when_cache_complete(db, r):
    await make_user(db, discord_id=A, username="a")
    await r.sadd(VISIBLE_USERS_KEY, A)
    await membership.rebuild_snapshot(db, r, [_guild(1, [])])
    assert await r.scard(VISIBLE_USERS_KEY) == 0


@pytest.mark.parametrize("flags", [{"chunked": False}, {"unavailable": True}])
async def test_rebuild_with_incomplete_cache_keeps_previous(db, r, flags):
    for uid in (A, B):
        await make_user(db, discord_id=uid, username=uid)
    await r.sadd(VISIBLE_USERS_KEY, A, "999")  # 999: no longer a registered user
    guilds = [_guild(1, [], **flags), _guild(2, [B])]

    await membership.rebuild_snapshot(db, r, guilds)
    assert await r.smembers(VISIBLE_USERS_KEY) == {A, B}


async def test_guild_join_chunks_then_rebuilds():
    guild = SimpleNamespace(id=1, chunked=False, chunk=AsyncMock())
    rebuild = AsyncMock()
    await membership.on_guild_join(guild, rebuild)
    guild.chunk.assert_awaited_once()
    rebuild.assert_awaited_once()


async def test_guild_join_chunk_failure_still_rebuilds_as_partial(db, r):
    await make_user(db, discord_id=A, username="a")
    guild = SimpleNamespace(
        id=1, chunked=False, unavailable=False, get_member=lambda uid: None,
        chunk=AsyncMock(side_effect=TimeoutError),
    )
    await membership.on_guild_join(guild, lambda: membership.rebuild_snapshot(db, r, [guild]))
    assert await r.get(MEMBERSHIP_COMPLETE_KEY) == "0"


async def test_rebuild_with_no_guilds_is_partial(db, r):
    await make_user(db, discord_id=A, username="a")
    await r.sadd(VISIBLE_USERS_KEY, A)
    await membership.rebuild_snapshot(db, r, [])
    assert await r.get(MEMBERSHIP_COMPLETE_KEY) == "0"
    assert await r.sismember(VISIBLE_USERS_KEY, A)


async def test_guild_join_already_chunked_skips_chunk():
    guild = SimpleNamespace(id=1, chunked=True, chunk=AsyncMock())
    rebuild = AsyncMock()
    await membership.on_guild_join(guild, rebuild)
    guild.chunk.assert_not_awaited()
    rebuild.assert_awaited_once()


async def test_refresh_user_adds_when_visible(r):
    await membership.refresh_user(r, SimpleNamespace(guilds=[_guild(1, [A])]), A)
    assert await r.sismember(VISIBLE_USERS_KEY, A)


async def test_refresh_user_miss_never_removes(r):
    await r.sadd(VISIBLE_USERS_KEY, A)
    await membership.refresh_user(r, SimpleNamespace(guilds=[_guild(1, [], chunked=False)]), A)
    assert await r.sismember(VISIBLE_USERS_KEY, A)


@pytest.mark.parametrize("flags", [{"chunked": False}, {"unavailable": True}])
async def test_member_remove_kept_when_other_guild_cache_incomplete(r, flags):
    await r.sadd(VISIBLE_USERS_KEY, A)
    left = _guild(1, [A])
    await membership.on_member_remove(r, [left, _guild(2, [], **flags)], _member(A, left))
    assert await r.sismember(VISIBLE_USERS_KEY, A)


async def test_member_remove_ignores_completeness_of_the_left_guild(r):
    await r.sadd(VISIBLE_USERS_KEY, A)
    left = _guild(1, [A], chunked=False)
    await membership.on_member_remove(r, [left, _guild(2, [])], _member(A, left))
    assert not await r.sismember(VISIBLE_USERS_KEY, A)


async def test_rebuild_marks_complete_or_partial(db, r):
    await make_user(db, discord_id=A, username="a")
    await membership.rebuild_snapshot(db, r, [_guild(1, [A])])
    assert await r.get(MEMBERSHIP_COMPLETE_KEY) == "1"
    await membership.rebuild_snapshot(db, r, [_guild(1, [A]), _guild(2, [], chunked=False)])
    assert await r.get(MEMBERSHIP_COMPLETE_KEY) == "0"


async def test_rebuild_synced_at_is_scan_start(db, r, monkeypatch):
    await make_user(db, discord_id=A, username="a")
    monkeypatch.setattr(membership.time, "time", lambda: 1000.0)
    await membership.rebuild_snapshot(db, r, [_guild(1, [A])])
    assert await r.get("bot:membership_synced_at") == "1000"


async def test_rebuild_and_incremental_ops_are_serialized(db, r, monkeypatch):
    """A join that arrives mid-rebuild waits and is applied after the swap."""
    await make_user(db, discord_id=A, username="a")
    await make_user(db, discord_id=B, username="b")
    gate = asyncio.Event()
    real_execute = db.execute

    async def slow_execute(*a, **kw):
        res = await real_execute(*a, **kw)
        await gate.wait()
        return res

    monkeypatch.setattr(db, "execute", slow_execute)
    guilds = [_guild(1, [A])]  # B joins after the rebuild scanned the cache
    rebuild = asyncio.create_task(membership.rebuild_snapshot(db, r, guilds))
    await asyncio.sleep(0)
    join = asyncio.create_task(membership.refresh_user(r, SimpleNamespace(guilds=[_guild(1, [A, B])]), B))
    await asyncio.sleep(0.01)
    assert not join.done()  # blocked on the lock
    gate.set()
    await asyncio.gather(rebuild, join)
    assert await r.smembers(VISIBLE_USERS_KEY) == {A, B}


async def test_refresh_user_never_raises(r):
    await membership.refresh_user(r, SimpleNamespace(), A)


async def test_refresh_user_skips_demo(r):
    client = SimpleNamespace(guilds=[_guild(1, [DEMO_DISCORD_ID])])
    await membership.refresh_user(r, client, DEMO_DISCORD_ID)
    assert await r.scard(VISIBLE_USERS_KEY) == 0


def test_is_visible_rejects_non_numeric_id():
    assert membership.is_visible([_guild(1, [A])], "not-a-snowflake") is False
