from datetime import datetime, timedelta, timezone

from app.models.session import SessionSource, SessionStatus
from tests.factories import (
    dt,
    make_game,
    make_pref,
    make_session,
    make_user,
)


def _cells_by_key(cells):
    return {(c["dow"], c["hour"]): c["seconds"] for c in cells}


# ── Empty / shape ─────────────────────────────────────────────────────────────

async def test_heatmap_empty_user_returns_168_zero_cells(authed_client):
    resp = await authed_client.get("/api/v1/stats/heatmap")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["cells"]) == 168
    assert all(c["seconds"] == 0 for c in data["cells"])
    # All 168 (dow, hour) keys present
    keys = {(c["dow"], c["hour"]) for c in data["cells"]}
    assert keys == {(d, h) for d in range(7) for h in range(24)}


# ── Bucketing & timezone ──────────────────────────────────────────────────────

async def test_heatmap_completed_session_buckets_correctly(authed_client, db, user):
    # 2026-04-15 is a Wednesday → Mon=0 spec → dow=2
    game = await make_game(db)
    start = datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc)
    end = start + timedelta(seconds=3600)
    await make_session(db, user.discord_id, game.id, start, end)

    resp = await authed_client.get("/api/v1/stats/heatmap")

    assert resp.status_code == 200
    cells = _cells_by_key(resp.json()["cells"])
    assert cells[(2, 14)] == 3600
    # All other cells zero
    other_total = sum(v for k, v in cells.items() if k != (2, 14))
    assert other_total == 0


async def test_heatmap_respects_user_timezone(authed_client, db, user):
    # Set user tz to America/New_York (UTC-4 in April)
    user.timezone = "America/New_York"
    db.add(user)
    await db.flush()

    game = await make_game(db)
    start = datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc)  # 10:30 local
    end = start + timedelta(seconds=3600)
    await make_session(db, user.discord_id, game.id, start, end)

    resp = await authed_client.get("/api/v1/stats/heatmap")

    assert resp.status_code == 200
    cells = _cells_by_key(resp.json()["cells"])
    # Wed 14:30 UTC → Wed 10:30 EDT → dow=2, hour=10
    assert cells[(2, 10)] == 3600
    assert cells[(2, 14)] == 0


# ── Exclusions ────────────────────────────────────────────────────────────────

async def test_heatmap_excludes_error_sessions(authed_client, db, user):
    game = await make_game(db)
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.get("/api/v1/stats/heatmap")

    assert resp.status_code == 200
    assert all(c["seconds"] == 0 for c in resp.json()["cells"])


async def test_heatmap_excludes_deleted_sessions(authed_client, db, user):
    game = await make_game(db)
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.get("/api/v1/stats/heatmap")

    assert resp.status_code == 200
    assert all(c["seconds"] == 0 for c in resp.json()["cells"])


async def test_heatmap_excludes_ignored_games(authed_client, db, user):
    game = await make_game(db)
    await make_pref(db, user.discord_id, game.id, is_ignored=True)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4))

    resp = await authed_client.get("/api/v1/stats/heatmap")

    assert resp.status_code == 200
    assert all(c["seconds"] == 0 for c in resp.json()["cells"])


# ── ONGOING included ──────────────────────────────────────────────────────────

async def test_heatmap_includes_ongoing(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    await make_session(
        db, user.discord_id, game.id, start,
        end_time=None,
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    resp = await authed_client.get("/api/v1/stats/heatmap")

    assert resp.status_code == 200
    cells = resp.json()["cells"]
    total = sum(c["seconds"] for c in cells)
    # ~1800s with tolerance for execution time
    assert 1700 <= total <= 1900


# ── DOW mapping ───────────────────────────────────────────────────────────────

async def test_heatmap_dow_mapping_monday_is_zero(authed_client, db, user):
    # 2026-04-13 is a Monday
    game = await make_game(db)
    mon = datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc)
    await make_session(
        db, user.discord_id, game.id, mon, mon + timedelta(seconds=600)
    )
    # 2026-04-19 is a Sunday
    sun = datetime(2026, 4, 19, 8, 0, tzinfo=timezone.utc)
    await make_session(
        db, user.discord_id, game.id, sun, sun + timedelta(seconds=900)
    )

    resp = await authed_client.get("/api/v1/stats/heatmap")

    assert resp.status_code == 200
    cells = _cells_by_key(resp.json()["cells"])
    assert cells[(0, 12)] == 600   # Monday 12:00
    assert cells[(6, 8)] == 900    # Sunday 08:00


# ── Window enforcement ────────────────────────────────────────────────────────

async def test_heatmap_excludes_old_sessions(authed_client, db, user):
    game = await make_game(db)
    # 100 days ago — outside default 90-day window
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=100 * 24), dt(hours_ago=100 * 24 - 1),
    )

    resp = await authed_client.get("/api/v1/stats/heatmap?days=90")

    assert resp.status_code == 200
    assert all(c["seconds"] == 0 for c in resp.json()["cells"])


# ── Auth ──────────────────────────────────────────────────────────────────────

async def test_heatmap_returns_unauthorized_without_token(client):
    resp = await client.get(
        "/api/v1/stats/heatmap", headers={"Authorization": "Bearer badtoken"}
    )

    assert resp.status_code == 401
