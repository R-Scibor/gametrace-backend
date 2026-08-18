from datetime import UTC, timedelta

from app.models.session import SessionSource, SessionStatus
from tests.factories import dt, make_game, make_session, make_user

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
    from datetime import datetime
    window_start = datetime.now(UTC) - timedelta(days=7) + timedelta(seconds=10)
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


async def test_negative_days_rejected(authed_client):
    resp = await authed_client.get("/api/v1/stats/summary?days=-1")

    assert resp.status_code == 422


# ── All-time (days=0) ─────────────────────────────────────────────────────────

async def test_days_zero_is_all_time(authed_client, db, user):
    game = await make_game(db)
    # A session ~40 days ago is outside any finite window but must count for all-time.
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=40 * 24), dt(hours_ago=40 * 24 - 1),
    )

    resp = await authed_client.get("/api/v1/stats/summary?days=0")

    assert resp.status_code == 200
    data = resp.json()
    assert data["days"] == 0
    assert data["total_seconds"] == 3600
    assert data["window_start"] is None  # no lower bound in all-time mode


async def test_all_time_has_no_previous_window(authed_client, db, user):
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=40 * 24), dt(hours_ago=40 * 24 - 1),
    )

    resp = await authed_client.get("/api/v1/stats/summary?days=0")

    assert resp.status_code == 200
    # There is no "period before all-time", so the delta baseline is zero.
    assert resp.json()["previous_total_seconds"] == 0


# ── Insight fields (avg / longest / previous / new games) ─────────────────────

async def test_empty_user_insight_fields_zeroed(authed_client):
    resp = await authed_client.get("/api/v1/stats/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["avg_session_seconds"] == 0
    assert data["longest_session_seconds"] == 0
    assert data["longest_session_game_id"] is None
    assert data["longest_session_game_name"] is None
    assert data["previous_total_seconds"] == 0
    assert data["new_games_count"] == 0


async def test_avg_session_seconds_completed_only(authed_client, db, user):
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4))  # 3600s
    await make_session(db, user.discord_id, game.id, dt(hours_ago=4), dt(hours_ago=2))  # 7200s
    # ONGOING must not drag the average
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=1),
        status=SessionStatus.ONGOING, source=SessionSource.BOT,
    )

    resp = await authed_client.get("/api/v1/stats/summary")

    assert resp.status_code == 200
    assert resp.json()["avg_session_seconds"] == 5400  # (3600 + 7200) / 2


async def test_longest_session_reports_game(authed_client, db, user):
    short_game = await make_game(db, primary_name="Shorty")
    long_game = await make_game(db, primary_name="Marathon")
    await make_session(db, user.discord_id, short_game.id, dt(hours_ago=5), dt(hours_ago=4))  # 3600s
    await make_session(db, user.discord_id, long_game.id, dt(hours_ago=10), dt(hours_ago=7))  # 10800s

    resp = await authed_client.get("/api/v1/stats/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["longest_session_seconds"] == 10800
    assert data["longest_session_game_id"] == long_game.id
    assert data["longest_session_game_name"] == "Marathon"


async def test_previous_total_seconds_prior_window(authed_client, db, user):
    game = await make_game(db)
    # current window (last 7d): 1h two days ago
    await make_session(db, user.discord_id, game.id, dt(hours_ago=48), dt(hours_ago=47))
    # previous window [now-14d, now-7d): 1h ten days ago
    await make_session(db, user.discord_id, game.id, dt(hours_ago=240), dt(hours_ago=239))

    resp = await authed_client.get("/api/v1/stats/summary?days=7")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_seconds"] == 3600
    assert data["previous_total_seconds"] == 3600


async def test_new_games_count_first_play_in_window(authed_client, db, user):
    new_game = await make_game(db, primary_name="Fresh")
    old_game = await make_game(db, primary_name="Veteran")
    # New game: only ever played inside the window
    await make_session(db, user.discord_id, new_game.id, dt(hours_ago=48), dt(hours_ago=47))
    # Old game: first played 30 days ago (before window) AND again inside it
    await make_session(db, user.discord_id, old_game.id, dt(hours_ago=720), dt(hours_ago=719))
    await make_session(db, user.discord_id, old_game.id, dt(hours_ago=24), dt(hours_ago=23))

    resp = await authed_client.get("/api/v1/stats/summary?days=7")

    assert resp.status_code == 200
    assert resp.json()["new_games_count"] == 1  # only the genuinely new game


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
