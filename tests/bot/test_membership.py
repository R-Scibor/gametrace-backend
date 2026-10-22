"""Bot-side maintenance of the Redis visibility snapshot."""
from types import SimpleNamespace

import fakeredis.aioredis
import pytest

from app.bot import membership
from app.core.bot_membership import VISIBLE_USERS_KEY
from app.services.demo import DEMO_DISCORD_ID
from tests.factories import make_user

A, B, C = "100000000000000001", "100000000000000002", "100000000000000003"


def _guild(gid, member_ids):
    ids = {int(m) for m in member_ids}
    return SimpleNamespace(id=gid, get_member=lambda uid: object() if uid in ids else None)


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


async def test_member_remove_kept_when_in_other_guild(db, r):
    await make_user(db, discord_id=A, username="a")
    await r.sadd(VISIBLE_USERS_KEY, A)
    left, other = _guild(1, [A]), _guild(2, [A])

    await membership.on_member_remove(db, r, [left, other], _member(A, left))
    assert await r.sismember(VISIBLE_USERS_KEY, A)


async def test_member_remove_dropped_when_no_other_guild(db, r):
    await make_user(db, discord_id=A, username="a")
    await r.sadd(VISIBLE_USERS_KEY, A)
    left = _guild(1, [A])  # discord.py may still return the member in the cache

    await membership.on_member_remove(db, r, [left, _guild(2, [])], _member(A, left))
    assert not await r.sismember(VISIBLE_USERS_KEY, A)


async def test_member_remove_unregistered_ignored(db, r):
    await r.sadd(VISIBLE_USERS_KEY, B)
    g = _guild(1, [])
    await membership.on_member_remove(db, r, [g], _member(B, g))
    assert await r.sismember(VISIBLE_USERS_KEY, B)  # untouched


async def test_refresh_user_add_and_remove(r):
    client = SimpleNamespace(guilds=[_guild(1, [A])])
    await membership.refresh_user(r, client, A)
    assert await r.sismember(VISIBLE_USERS_KEY, A)

    client.guilds = []
    await membership.refresh_user(r, client, A)
    assert not await r.sismember(VISIBLE_USERS_KEY, A)


async def test_refresh_user_never_raises(r):
    await membership.refresh_user(r, SimpleNamespace(), A)


async def test_refresh_user_skips_demo(r):
    client = SimpleNamespace(guilds=[_guild(1, [DEMO_DISCORD_ID])])
    await membership.refresh_user(r, client, DEMO_DISCORD_ID)
    assert await r.scard(VISIBLE_USERS_KEY) == 0
