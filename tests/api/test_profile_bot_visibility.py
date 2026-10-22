"""bot_can_see_user and the bot-visibility reminder settings on /profile."""
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import fakeredis.aioredis
import pytest

from app.api.v1.endpoints import profile
from app.services.demo import DEMO_DISCORD_ID
from tests.factories import make_token, make_user

ME = "/api/v1/profile/me"
SETTINGS = "/api/v1/profile/settings"


@pytest.fixture
async def r(monkeypatch):
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(profile, "get_redis", lambda: client)
    return client


async def _bot_alive(r, *, heartbeat_age=0, started_ago=100, synced_ago=50):
    now = int(time.time())
    await r.set("bot:heartbeat", now - heartbeat_age)
    await r.set("bot:started_at", now - started_ago)
    if synced_ago is not None:
        await r.set("bot:membership_synced_at", now - synced_ago)


async def test_visible_user_is_true(authed_client, user, r):
    await _bot_alive(r)
    await r.sadd("bot:visible_users", user.discord_id)

    body = (await authed_client.get(ME)).json()
    assert body["bot_can_see_user"] is True


async def test_invisible_user_is_false(authed_client, r):
    await _bot_alive(r)
    await r.sadd("bot:visible_users", "999")

    assert (await authed_client.get(ME)).json()["bot_can_see_user"] is False


async def test_no_heartbeat_is_unknown(authed_client, user, r):
    await r.set("bot:started_at", int(time.time()) - 100)
    await r.set("bot:membership_synced_at", int(time.time()) - 50)

    assert (await authed_client.get(ME)).json()["bot_can_see_user"] is None


async def test_stale_heartbeat_is_unknown(authed_client, user, r):
    await _bot_alive(r, heartbeat_age=120)
    await r.sadd("bot:visible_users", user.discord_id)

    assert (await authed_client.get(ME)).json()["bot_can_see_user"] is None


async def test_missing_snapshot_is_unknown(authed_client, r):
    await _bot_alive(r, synced_ago=None)

    assert (await authed_client.get(ME)).json()["bot_can_see_user"] is None


async def test_snapshot_from_previous_process_is_unknown(authed_client, user, r):
    await _bot_alive(r, started_ago=10, synced_ago=500)
    await r.sadd("bot:visible_users", user.discord_id)

    assert (await authed_client.get(ME)).json()["bot_can_see_user"] is None


class _BrokenRedis:
    async def mget(self, *keys):
        raise ConnectionError("redis down")


async def test_redis_error_is_unknown(authed_client, monkeypatch):
    monkeypatch.setattr(profile, "get_redis", lambda: _BrokenRedis())

    resp = await authed_client.get(ME)
    assert resp.status_code == 200
    assert resp.json()["bot_can_see_user"] is None


async def test_demo_account_is_unknown(db, client, r):
    demo = await make_user(db, discord_id=DEMO_DISCORD_ID, username="demo")
    token = await make_token(db, demo.discord_id)
    await _bot_alive(r)
    await r.sadd("bot:visible_users", DEMO_DISCORD_ID)

    resp = await client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert resp.json()["bot_can_see_user"] is None


async def test_reminder_defaults(authed_client, r):
    body = (await authed_client.get(ME)).json()
    assert body["bot_visibility_reminders_enabled"] is True
    assert body["bot_visibility_reminder_snoozed_until"] is None


async def test_disable_reminders_persists(authed_client, r):
    resp = await authed_client.put(SETTINGS, json={"bot_visibility_reminders_enabled": False})
    assert resp.status_code == 200
    assert "bot_can_see_user" in resp.json()
    assert (await authed_client.get(ME)).json()["bot_visibility_reminders_enabled"] is False


async def test_snooze_normalized_to_utc_and_cleared_with_null(authed_client, r):
    local = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0).astimezone(
        ZoneInfo("Europe/Warsaw")
    )
    resp = await authed_client.put(
        SETTINGS, json={"bot_visibility_reminder_snoozed_until": local.isoformat()}
    )
    assert resp.status_code == 200
    stored = datetime.fromisoformat(resp.json()["bot_visibility_reminder_snoozed_until"])
    assert stored.utcoffset() == timedelta(0)
    assert stored == local

    resp = await authed_client.put(SETTINGS, json={"bot_visibility_reminder_snoozed_until": None})
    assert resp.status_code == 200
    assert resp.json()["bot_visibility_reminder_snoozed_until"] is None


async def test_snooze_omitted_leaves_it(authed_client, r):
    until = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    await authed_client.put(SETTINGS, json={"bot_visibility_reminder_snoozed_until": until})
    resp = await authed_client.put(SETTINGS, json={"language": "en"})
    assert resp.json()["bot_visibility_reminder_snoozed_until"] is not None


@pytest.mark.parametrize(
    "payload",
    [
        {"bot_visibility_reminder_snoozed_until": "2026-12-01T10:00:00"},
        {"bot_visibility_reminder_snoozed_until": (datetime.now(UTC) + timedelta(days=400)).isoformat()},
        {"bot_visibility_reminders_enabled": None},
    ],
    ids=["naive", "too-far", "null-flag"],
)
async def test_invalid_settings_rejected(authed_client, r, payload):
    resp = await authed_client.put(SETTINGS, json=payload)
    assert resp.status_code == 422
