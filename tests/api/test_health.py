"""Tests for the public health payload.

`grace_days` rides along here because the web app states the account-deletion
retention period on its logged-out /delete-account and /privacy pages — before
any deletion exists and with no token to authenticate — so it needs an
unauthenticated source for the number.
"""
import time

import fakeredis.aioredis
import pytest

from app.api.v1.endpoints import health
from app.core.config import settings

URL = "/api/v1/health"


@pytest.fixture
async def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _patch_redis(monkeypatch, redis_client) -> None:
    monkeypatch.setattr(health, "get_redis", lambda: redis_client)


async def test_missing_heartbeat_reports_no_uptime(client, redis_client, monkeypatch):
    _patch_redis(monkeypatch, redis_client)
    now = int(time.time())
    await redis_client.set("bot:started_at", now - 10_000)

    resp = await client.get(URL)

    assert resp.status_code == 200
    bot = resp.json()["bot"]
    assert bot["status"] == "offline"
    assert bot["uptime_seconds"] is None


async def test_stale_heartbeat_reports_no_uptime(client, redis_client, monkeypatch):
    _patch_redis(monkeypatch, redis_client)
    now = int(time.time())
    await redis_client.set("bot:started_at", now - 10_000)
    await redis_client.set("bot:heartbeat", now - 120)

    resp = await client.get(URL)

    assert resp.status_code == 200
    bot = resp.json()["bot"]
    assert bot["status"] == "offline"
    assert bot["uptime_seconds"] is None


async def test_fresh_heartbeat_reports_uptime(client, redis_client, monkeypatch):
    _patch_redis(monkeypatch, redis_client)
    now = int(time.time())
    await redis_client.set("bot:started_at", now - 120)
    await redis_client.set("bot:heartbeat", now)

    resp = await client.get(URL)

    assert resp.status_code == 200
    bot = resp.json()["bot"]
    assert bot["status"] == "online"
    assert bot["uptime_seconds"] is not None
    assert bot["uptime_seconds"] >= 100


async def test_health_is_reachable_without_a_token(client):
    resp = await client.get(URL)

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_health_exposes_grace_days(client):
    resp = await client.get(URL)

    assert resp.status_code == 200
    assert resp.json()["grace_days"] == settings.account_deletion_grace_days


async def test_health_grace_days_follows_the_setting(client, monkeypatch):
    monkeypatch.setattr(settings, "account_deletion_grace_days", 14)

    resp = await client.get(URL)

    assert resp.status_code == 200
    assert resp.json()["grace_days"] == 14
