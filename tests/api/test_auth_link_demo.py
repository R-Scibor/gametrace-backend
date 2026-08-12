"""API tests for the permanent reviewer (demo) code on POST /auth/link.

The demo branch must short-circuit before the link_code_secret guard, before
get_redis() and before check_lockout, and must never call redeem_code.

The load-bearing proof of that is test_demo_code_works_when_redis_unavailable:
with get_redis() patched to raise, any Redis contact whatsoever on the demo
path — check_lockout, redeem_code, anything — would surface as a 503.

Patch target for Redis: app.api.v1.endpoints.auth.get_redis
"""
import fakeredis.aioredis
import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.user import User, UserAuthToken
from app.services import link_codes
from app.services.demo import (
    DEMO_DISCORD_ID,
    DEMO_MAX_TOKENS,
    DEMO_TIMEZONE,
    DEMO_USERNAME,
)
from tests.factories import make_user

_SECRET = "test-link-code-secret"
_DEMO_CODE = "424242"  # test dummy, never the production value


@pytest.fixture
async def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def link_secret(monkeypatch):
    monkeypatch.setattr(settings, "link_code_secret", _SECRET)


@pytest.fixture(autouse=True)
def demo_code(monkeypatch):
    monkeypatch.setattr(settings, "demo_link_code", _DEMO_CODE)


@pytest.fixture(autouse=True)
def patch_get_redis(monkeypatch, redis_client):
    monkeypatch.setattr("app.api.v1.endpoints.auth.get_redis", lambda: redis_client)


@pytest.fixture
async def demo_user(db):
    """Migration 0020 seeds this row in prod; tests build the schema with
    create_all, so it has to be created here."""
    return await make_user(
        db,
        discord_id=DEMO_DISCORD_ID,
        username=DEMO_USERNAME,
        tz=DEMO_TIMEZONE,
    )


async def _recorded_failures(redis_client) -> int:
    """Total per-IP failure counters recorded (the client IP httpx presents
    varies, so match on the key prefix)."""
    total = 0
    for key in await redis_client.keys("link:fails:ip:*"):
        total += int(await redis_client.get(key))
    return total


async def _token_count(db) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(UserAuthToken)
        .where(UserAuthToken.user_id == DEMO_DISCORD_ID)
    )
    return result.scalar_one()


async def test_demo_code_logs_in(client, demo_user):
    resp = await client.post("/api/v1/auth/link", json={"code": _DEMO_CODE})

    assert resp.status_code == 200
    data = resp.json()
    assert data["discord_id"] == DEMO_DISCORD_ID
    assert data["username"] == DEMO_USERNAME
    assert data["is_admin"] is False
    assert data["needs_server_join"] is False
    assert data["pending_deletion"] is None
    assert "token" in data


async def test_demo_token_authenticates(client, demo_user):
    resp = await client.post("/api/v1/auth/link", json={"code": _DEMO_CODE})
    assert resp.status_code == 200

    protected = await client.get(
        "/api/v1/stats/summary",
        headers={"Authorization": f"Bearer {resp.json()['token']}"},
    )
    assert protected.status_code == 200


async def test_demo_code_is_reusable(client, demo_user):
    for _ in range(3):
        resp = await client.post("/api/v1/auth/link", json={"code": _DEMO_CODE})
        assert resp.status_code == 200
        assert resp.json()["discord_id"] == DEMO_DISCORD_ID


async def test_demo_code_works_with_link_secret_unset(client, demo_user, monkeypatch):
    monkeypatch.setattr(settings, "link_code_secret", "")

    resp = await client.post("/api/v1/auth/link", json={"code": _DEMO_CODE})

    assert resp.status_code == 200
    assert resp.json()["discord_id"] == DEMO_DISCORD_ID


async def test_demo_code_works_when_redis_unavailable(client, demo_user, monkeypatch):
    """The demo path never touches Redis, and therefore never reaches
    redeem_code. get_redis() raising means any Redis contact at all — the
    lockout check, a GETDEL of the submitted code, anything — would 503
    instead of returning 200. This is the strongest proof of that property
    in the file; the lockout tests below do not establish it."""
    def _raise():
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.api.v1.endpoints.auth.get_redis", _raise)

    resp = await client.post("/api/v1/auth/link", json={"code": _DEMO_CODE})

    assert resp.status_code == 200
    assert resp.json()["discord_id"] == DEMO_DISCORD_ID


async def test_demo_code_succeeds_while_ip_locked_out(client, demo_user, redis_client):
    for _ in range(link_codes.IP_FAIL_LIMIT):
        resp = await client.post("/api/v1/auth/link", json={"code": "000000"})
        assert resp.status_code == 401

    locked = await client.post("/api/v1/auth/link", json={"code": "000000"})
    assert locked.status_code == 429

    resp = await client.post("/api/v1/auth/link", json={"code": _DEMO_CODE})
    assert resp.status_code == 200
    assert resp.json()["discord_id"] == DEMO_DISCORD_ID


async def test_locked_out_ip_does_not_consume_real_code(client, user, demo_user, redis_client):
    """Lockout regression guard for the NORMAL path: check_lockout still runs
    before redeem_code, so a locked-out request never consumes a live code —
    the Redis value survives, which a GETDEL would have destroyed.

    This says nothing about the demo branch: the code submitted here is a
    valid ephemeral one, so an implementation that called redeem_code first
    and let the demo case fall out would pass this test identically. The
    demo-branch property is proven by
    test_demo_code_works_when_redis_unavailable."""
    code = await link_codes.issue_code(redis_client, user.discord_id)
    digest_key = await redis_client.get(link_codes.user_key(user.discord_id))
    assert digest_key is not None

    global_key = link_codes.global_fail_key()
    await redis_client.set(global_key, str(link_codes.GLOBAL_FAIL_LIMIT))
    await redis_client.expire(global_key, link_codes.GLOBAL_FAIL_WINDOW_SECONDS)

    resp = await client.post("/api/v1/auth/link", json={"code": code})

    assert resp.status_code == 429
    assert await redis_client.get(digest_key) == user.discord_id


async def test_wrong_code_still_401_and_records_failure(client, demo_user, redis_client):
    resp = await client.post("/api/v1/auth/link", json={"code": "000000"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired code"
    assert await _recorded_failures(redis_client) == 1


async def test_demo_login_ignores_supplied_timezone(client, db, demo_user):
    resp = await client.post(
        "/api/v1/auth/link",
        json={"code": _DEMO_CODE, "timezone": "America/New_York"},
    )

    assert resp.status_code == 200
    assert resp.json()["timezone"] == DEMO_TIMEZONE
    refreshed = await db.get(User, DEMO_DISCORD_ID)
    await db.refresh(refreshed)
    assert refreshed.timezone == DEMO_TIMEZONE


async def test_token_cap_keeps_only_most_recent(client, db, user, demo_user):
    """Covers the `id DESC` half of the cap ordering only. The harness runs a
    test inside one transaction, so server_default func.now() gives every
    token here the same created_at; the `created_at DESC` component that
    orders tokens across separate requests in production is not exercised."""
    other_before = await _other_user_token_count(db, user.discord_id)

    for _ in range(DEMO_MAX_TOKENS + 2):
        resp = await client.post("/api/v1/auth/link", json={"code": _DEMO_CODE})
        assert resp.status_code == 200
        last_token = resp.json()["token"]

    assert await _token_count(db) == DEMO_MAX_TOKENS

    # the newest token survived the cap
    protected = await client.get(
        "/api/v1/stats/summary",
        headers={"Authorization": f"Bearer {last_token}"},
    )
    assert protected.status_code == 200

    # no other account's tokens were touched
    assert await _other_user_token_count(db, user.discord_id) == other_before


async def _other_user_token_count(db, user_id: str) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(UserAuthToken)
        .where(UserAuthToken.user_id == user_id)
    )
    return result.scalar_one()


async def test_other_users_tokens_untouched_by_cap(client, db, user, demo_user):
    from tests.factories import make_token

    for _ in range(DEMO_MAX_TOKENS + 2):
        await make_token(db, user.discord_id)

    for _ in range(DEMO_MAX_TOKENS + 2):
        assert (await client.post("/api/v1/auth/link", json={"code": _DEMO_CODE})).status_code == 200

    assert await _other_user_token_count(db, user.discord_id) == DEMO_MAX_TOKENS + 2


async def test_feature_inert_when_demo_code_empty(client, demo_user, monkeypatch, redis_client):
    monkeypatch.setattr(settings, "demo_link_code", "")

    resp = await client.post("/api/v1/auth/link", json={"code": _DEMO_CODE})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired code"
    assert await _recorded_failures(redis_client) == 1


async def test_missing_demo_user_returns_401(client):
    """Crash guard: a missing demo users row 401s rather than raising. It
    cannot distinguish the demo branch from the normal path — an unknown code
    401s either way."""
    resp = await client.post("/api/v1/auth/link", json={"code": _DEMO_CODE})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired code"
