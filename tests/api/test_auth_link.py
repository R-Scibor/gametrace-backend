"""API tests for POST /auth/link.

Patch target for Redis: app.api.v1.endpoints.auth.get_redis
"""
import fakeredis.aioredis
import pytest

from app.core.config import settings
from app.services import link_codes

_SECRET = "test-link-code-secret"


@pytest.fixture
async def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def link_secret(monkeypatch):
    monkeypatch.setattr(settings, "link_code_secret", _SECRET)


@pytest.fixture(autouse=True)
def patch_get_redis(monkeypatch, redis_client):
    monkeypatch.setattr("app.api.v1.endpoints.auth.get_redis", lambda: redis_client)


async def test_link_happy_path_token_works(client, db, user, redis_client):
    code = await link_codes.issue_code(redis_client, user.discord_id)

    resp = await client.post("/api/v1/auth/link", json={"code": code})

    assert resp.status_code == 200
    data = resp.json()
    assert data["discord_id"] == user.discord_id
    assert data["username"] == user.username
    assert data["needs_server_join"] is False
    assert "token" in data

    protected = await client.get(
        "/api/v1/stats/summary",
        headers={"Authorization": f"Bearer {data['token']}"},
    )
    assert protected.status_code == 200


async def test_wrong_code_returns_401(client, user, redis_client):
    await link_codes.issue_code(redis_client, user.discord_id)

    resp = await client.post("/api/v1/auth/link", json={"code": "000000"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired code"


async def test_expired_code_returns_401(client, user, redis_client):
    code = await link_codes.issue_code(redis_client, user.discord_id)
    digest_key = await redis_client.get(link_codes.user_key(user.discord_id))
    await redis_client.expire(digest_key, 0)

    resp = await client.post("/api/v1/auth/link", json={"code": code})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired code"


async def test_second_redemption_returns_401(client, user, redis_client):
    code = await link_codes.issue_code(redis_client, user.discord_id)

    first = await client.post("/api/v1/auth/link", json={"code": code})
    assert first.status_code == 200

    second = await client.post("/api/v1/auth/link", json={"code": code})
    assert second.status_code == 401
    assert second.json()["detail"] == "Invalid or expired code"


async def test_per_ip_lockout_returns_429_with_retry_after(client, user, redis_client):
    await link_codes.issue_code(redis_client, user.discord_id)

    for _ in range(link_codes.IP_FAIL_LIMIT):
        resp = await client.post("/api/v1/auth/link", json={"code": "000000"})
        assert resp.status_code == 401

    resp = await client.post("/api/v1/auth/link", json={"code": "000000"})
    assert resp.status_code == 429
    assert "retry-after" in {k.lower() for k in resp.headers}
    retry_after = int(resp.headers["Retry-After"])
    assert retry_after >= 1


async def test_global_lockout_returns_429(client, user, redis_client):
    await link_codes.issue_code(redis_client, user.discord_id)
    global_key = link_codes.global_fail_key()
    await redis_client.set(global_key, str(link_codes.GLOBAL_FAIL_LIMIT))
    await redis_client.expire(global_key, link_codes.GLOBAL_FAIL_WINDOW_SECONDS)

    resp = await client.post("/api/v1/auth/link", json={"code": "000000"})

    assert resp.status_code == 429
    assert "retry-after" in {k.lower() for k in resp.headers}


@pytest.mark.parametrize("code", ["      ", "12345", "abcdef"])
async def test_malformed_code_returns_422(client, code):
    resp = await client.post("/api/v1/auth/link", json={"code": code})

    assert resp.status_code == 422


async def test_timezone_non_utc_applied(client, db, user, redis_client):
    code = await link_codes.issue_code(redis_client, user.discord_id)

    resp = await client.post(
        "/api/v1/auth/link",
        json={"code": code, "timezone": "Europe/Warsaw"},
    )

    assert resp.status_code == 200
    assert resp.json()["timezone"] == "Europe/Warsaw"


async def test_empty_secret_returns_503(client, user, redis_client, monkeypatch):
    await link_codes.issue_code(redis_client, user.discord_id)
    monkeypatch.setattr(settings, "link_code_secret", "")

    resp = await client.post("/api/v1/auth/link", json={"code": "123456"})

    assert resp.status_code == 503


async def test_redis_unavailable_returns_503(client, user, redis_client, monkeypatch):
    code = await link_codes.issue_code(redis_client, user.discord_id)

    def _raise_connection_error():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr("app.api.v1.endpoints.auth.get_redis", _raise_connection_error)

    resp = await client.post("/api/v1/auth/link", json={"code": code})

    assert resp.status_code == 503
    assert resp.json()["detail"] == "Service temporarily unavailable"


async def test_deleted_user_returns_401_without_extra_failure(
    client, db, user, redis_client
):
    code = await link_codes.issue_code(redis_client, user.discord_id)
    await db.delete(user)
    await db.flush()
    assert await redis_client.keys("link:fails:*") == []

    resp = await client.post("/api/v1/auth/link", json={"code": code})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired code"
    assert await redis_client.keys("link:fails:*") == []