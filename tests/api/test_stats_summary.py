from datetime import timedelta, timezone

from app.models.session import SessionSource, SessionStatus
from tests.factories import dt, make_game, make_session, make_token, make_user


# ── Basic aggregation ─────────────────────────────────────────────────────────

async def test_totals_completed_sessions_only(authed_client, db, user):
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))  # 3600s
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.get("/api/v1/stats/summary")

    assert resp.status_code == 200
    assert resp.json()["total_seconds"] == 3600


async def test_excludes_ongoing_from_totals(authed_client, db, user):
    game = await make_game(db)
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=2),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    resp = await authed_client.get("/api/v1/stats/summary")

    assert resp.status_code == 200
    assert resp.json()["total_seconds"] == 0


async def test_empty_user_returns_zeros(authed_client):
    resp = await authed_client.get("/api/v1/stats/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_seconds"] == 0
    assert data["per_game"] == []
    assert data["pending_errors"] == []


# ── Window boundary ───────────────────────────────────────────────────────────

async def test_session_at_window_start_is_included(authed_client, db, user):
    """Session whose start_time is just inside the window boundary must be included (>=)."""
    game = await make_game(db)
    # Place session 10 seconds inside the 7-day window to avoid sub-second timing races
    # between test setup and the endpoint's own `now` computation.
    from datetime import datetime, timezone as tz
    window_start = datetime.now(tz.utc) - timedelta(days=7) + timedelta(seconds=10)
    await make_session(
        db, user.discord_id, game.id,
        window_start,
        window_start + timedelta(hours=1),
    )

    resp = await authed_client.get("/api/v1/stats/summary?days=7")

    assert resp.status_code == 200
    assert resp.json()["total_seconds"] == 3600


async def test_session_before_window_excluded(authed_client, db, user):
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=241), dt(hours_ago=240))  # ~10d ago

    resp = await authed_client.get("/api/v1/stats/summary?days=7")

    assert resp.status_code == 200
    assert resp.json()["total_seconds"] == 0


# ── per_game ordering ─────────────────────────────────────────────────────────

async def test_per_game_sorted_by_total_desc(authed_client, db, user):
    game_a = await make_game(db, "Game A")
    game_b = await make_game(db, "Game B")
    game_c = await make_game(db, "Game C")

    # Game C: 3h, Game A: 2h, Game B: 1h
    await make_session(db, user.discord_id, game_c.id, dt(hours_ago=10), dt(hours_ago=7))
    await make_session(db, user.discord_id, game_a.id, dt(hours_ago=6), dt(hours_ago=4))
    await make_session(db, user.discord_id, game_b.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/stats/summary")

    assert resp.status_code == 200
    per_game = resp.json()["per_game"]
    assert len(per_game) == 3
    assert per_game[0]["game_name"] == "Game C"
    assert per_game[1]["game_name"] == "Game A"
    assert per_game[2]["game_name"] == "Game B"


# ── pending_errors ────────────────────────────────────────────────────────────

async def test_pending_errors_listed(authed_client, db, user):
    game = await make_game(db)
    err1 = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
        status=SessionStatus.ERROR, notes="bot restarted",
    )
    err2 = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2),
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.get("/api/v1/stats/summary")

    assert resp.status_code == 200
    errors = resp.json()["pending_errors"]
    assert len(errors) == 2
    ids = {e["id"] for e in errors}
    assert err1.id in ids
    assert err2.id in ids
    # Fields present
    for e in errors:
        assert "id" in e
        assert "game_name" in e
        assert "start_time" in e


# ── Query param validation ────────────────────────────────────────────────────

async def test_days_default_is_7(authed_client, db, user):
    game = await make_game(db)
    # Session 8 days ago — outside default 7-day window
    await make_session(db, user.discord_id, game.id, dt(hours_ago=193), dt(hours_ago=192))

    resp = await authed_client.get("/api/v1/stats/summary")  # no ?days=

    assert resp.status_code == 200
    assert resp.json()["days"] == 7
    assert resp.json()["total_seconds"] == 0  # 8-day-old session excluded


async def test_days_max_365_enforced(authed_client):
    resp = await authed_client.get("/api/v1/stats/summary?days=366")

    assert resp.status_code == 422


# ── Multi-user isolation ──────────────────────────────────────────────────────

async def test_other_users_data_excluded(authed_client, db, user):
    game = await make_game(db)
    other = await make_user(db, discord_id="222222222222222222", username="otheruser")

    # authed user: 1h session
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    # other user: 5h session
    await make_session(db, other.discord_id, game.id, dt(hours_ago=10), dt(hours_ago=5))

    resp = await authed_client.get("/api/v1/stats/summary")

    assert resp.status_code == 200
    assert resp.json()["total_seconds"] == 3600  # only own session counted
