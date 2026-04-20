"""Weekly report fan-out — unit tests.

Async tests call _run_weekly_report(db) directly with a mocked Redis + mocked
send_to_user so nothing actually talks to FCM or Redis. The sync .run() entry
point isn't exercised here — it only wraps _run_with_engine in asyncio.run.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.celery_app import celery_app
from app.tasks import weekly_report as wr
from tests.factories import make_game, make_session, make_user


class FakeRedis:
    """Minimal stand-in: .set(key, val, nx, ex) honoring NX semantics."""

    def __init__(self, preset: set[str] | None = None):
        self._keys: set[str] = set(preset or ())
        self.calls: list[str] = []

    def set(self, key, _val, nx=False, ex=None):
        self.calls.append(key)
        if nx and key in self._keys:
            return None
        self._keys.add(key)
        return True


@pytest.fixture
def patch_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(wr.redis_sync, "from_url", lambda *a, **k: fake)
    return fake


@pytest.fixture
def mock_send(monkeypatch):
    m = AsyncMock(return_value=1)
    monkeypatch.setattr(wr, "send_to_user", m)
    return m


async def test_skips_user_with_weekly_report_disabled(
    db, patch_redis, mock_send
):
    user = await make_user(
        db,
        discord_id="900000000000000001",
        username="disabled_wr",
    )
    user.weekly_report_enabled = False
    await db.flush()

    sent = await wr._run_weekly_report(db)

    assert sent == 0
    mock_send.assert_not_called()


async def test_skips_user_with_push_disabled(db, patch_redis, mock_send):
    user = await make_user(
        db, discord_id="900000000000000002", username="no_push"
    )
    user.push_enabled = False
    await db.flush()

    sent = await wr._run_weekly_report(db)

    assert sent == 0
    mock_send.assert_not_called()


async def test_sends_to_enabled_user(db, patch_redis, mock_send):
    user = await make_user(
        db, discord_id="900000000000000003", username="enabled"
    )
    game = await make_game(db, primary_name="Hades")
    start = datetime.now(timezone.utc) - timedelta(days=2)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(hours=3)
    )

    sent = await wr._run_weekly_report(db)

    assert sent == 1
    mock_send.assert_awaited_once()
    args, kwargs = mock_send.call_args
    # positional: (db, user_id, title, body)
    assert args[1] == user.discord_id
    assert "weekly" in args[2].lower() or "weekly" in args[3].lower()
    assert kwargs.get("data", {}).get("type") == "weekly_report"


async def test_dedup_key_prevents_resend(db, mock_send, monkeypatch):
    user = await make_user(
        db, discord_id="900000000000000004", username="dedup"
    )
    now = datetime.now(timezone.utc)
    preset = {wr._dedup_key(user.discord_id, now)}
    fake = FakeRedis(preset=preset)
    monkeypatch.setattr(wr.redis_sync, "from_url", lambda *a, **k: fake)

    sent = await wr._run_weekly_report(db)

    assert sent == 0
    mock_send.assert_not_called()


async def test_payload_formats_top_game(db):
    user = await make_user(
        db, discord_id="900000000000000005", username="fmt"
    )
    game_a = await make_game(db, primary_name="Hollow Knight")
    start = datetime.now(timezone.utc) - timedelta(days=1)
    await make_session(
        db, user.discord_id, game_a.id, start, start + timedelta(hours=5)
    )

    from app.services.stats import summary_for_user

    summary = await summary_for_user(db, user, days=7)
    title, body = wr._format_payload(summary)

    assert "weekly report" in title.lower()
    assert "5h" in body
    assert "Hollow Knight" in body


def test_beat_schedule_has_weekly_report():
    sched = celery_app.conf.beat_schedule
    assert "weekly_report" in sched
    assert sched["weekly_report"]["task"] == "tasks.weekly_report"
