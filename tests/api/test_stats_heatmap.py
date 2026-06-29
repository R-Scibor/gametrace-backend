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

async def test_heatmap_session_split_across_spanned_hours(authed_client, db, user):
    # 2026-04-15 is a Wednesday → Mon=0 spec → dow=2.
    # A 14:30→15:30 session straddles the 15:00 boundary, so its 3600s split
    # 1800s into hour 14 and 1800s into hour 15.
    game = await make_game(db)
    start = datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc)
    end = start + timedelta(seconds=3600)
    await make_session(db, user.discord_id, game.id, start, end)

    resp = await authed_client.get("/api/v1/stats/heatmap")

    assert resp.status_code == 200
    cells = _cells_by_key(resp.json()["cells"])
    assert cells[(2, 14)] == 1800
    assert cells[(2, 15)] == 1800
    other_total = sum(v for k, v in cells.items() if k not in {(2, 14), (2, 15)})
    assert other_total == 0


async def test_heatmap_within_single_hour_stays_in_one_cell(authed_client, db, user):
    # 14:10→14:20 — entirely inside hour 14, so one cell, no split.
    game = await make_game(db)
    start = datetime(2026, 4, 15, 14, 10, tzinfo=timezone.utc)
    end = start + timedelta(seconds=600)
    await make_session(db, user.discord_id, game.id, start, end)

    resp = await authed_client.get("/api/v1/stats/heatmap")

    assert resp.status_code == 200
    cells = _cells_by_key(resp.json()["cells"])
    assert cells[(2, 14)] == 600
    assert sum(v for k, v in cells.items() if k != (2, 14)) == 0


async def test_heatmap_session_crosses_midnight_splits_dow(authed_client, db, user):
    # 23:00 Wed → 02:00 Thu (3h): 1h each into Wed 23:00, Thu 00:00, Thu 01:00.
    game = await make_game(db)
    start = datetime(2026, 4, 15, 23, 0, tzinfo=timezone.utc)  # Wed → dow=2
    end = start + timedelta(seconds=3 * 3600)
    await make_session(db, user.discord_id, game.id, start, end)

    resp = await authed_client.get("/api/v1/stats/heatmap")

    assert resp.status_code == 200
    cells = _cells_by_key(resp.json()["cells"])
    assert cells[(2, 23)] == 3600   # Wed 23:00
    assert cells[(3, 0)] == 3600    # Thu 00:00
    assert cells[(3, 1)] == 3600    # Thu 01:00
    # Conservation: every second lands in exactly one cell.
    assert sum(cells.values()) == 3 * 3600


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
    # Wed 14:30 UTC → Wed 10:30 EDT; the 1h session splits across local hours
    # 10 and 11 (not the UTC hour 14).
    assert cells[(2, 10)] == 1800
    assert cells[(2, 11)] == 1800
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


async def test_heatmap_days_zero_is_all_time(authed_client, db, user):
    game = await make_game(db)
    # 100 days ago — outside the default 90-day window, but all-time must count it.
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=100 * 24), dt(hours_ago=100 * 24 - 1),
    )

    resp = await authed_client.get("/api/v1/stats/heatmap?days=0")

    assert resp.status_code == 200
    data = resp.json()
    assert data["days"] == 0
    assert sum(c["seconds"] for c in data["cells"]) == 3600


# ── Auth ──────────────────────────────────────────────────────────────────────

async def test_heatmap_returns_unauthorized_without_token(client):
    resp = await client.get(
        "/api/v1/stats/heatmap", headers={"Authorization": "Bearer badtoken"}
    )

    assert resp.status_code == 401
