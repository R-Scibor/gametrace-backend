from datetime import UTC, datetime, timedelta

from app.models.session import SessionStatus
from tests.factories import dt, make_game, make_pref, make_session, make_user

URL = "/api/v1/games/{}/stats"


async def test_completed_sessions_total_count_and_dates(authed_client, db, user):
    game = await make_game(db)
    s1 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    await make_session(db, user.discord_id, game.id, s1, s1 + timedelta(seconds=3600))
    s2 = datetime(2026, 3, 5, 20, 0, tzinfo=UTC)
    await make_session(db, user.discord_id, game.id, s2, s2 + timedelta(seconds=1800))

    resp = await authed_client.get(URL.format(game.id))

    assert resp.status_code == 200
    data = resp.json()
    assert data["game_id"] == game.id
    assert data["total_seconds"] == 5400
    assert data["session_count"] == 2
    assert data["first_played"].startswith("2026-01-10")
    # last_played = max(end_time) = 2026-03-05 20:30
    assert data["last_played"].startswith("2026-03-05")


async def test_ongoing_session_counts_live(authed_client, db, user):
    game = await make_game(db)
    start = dt(hours_ago=2)  # 2h ago, no end_time
    await make_session(
        db, user.discord_id, game.id, start, end_time=None,
        status=SessionStatus.ONGOING,
    )

    resp = await authed_client.get(URL.format(game.id))

    assert resp.status_code == 200
    data = resp.json()
    assert data["session_count"] == 1
    # ~7200s elapsed; allow scheduling slack
    assert 7000 <= data["total_seconds"] <= 7400
    # last_played falls back to start_time for an ONGOING session
    assert data["last_played"] is not None


async def test_excludes_error_flicker_and_soft_deleted(authed_client, db, user):
    game = await make_game(db)
    good_start = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)
    await make_session(
        db, user.discord_id, game.id, good_start, good_start + timedelta(seconds=600)
    )
    # ERROR
    e = datetime(2026, 2, 2, 10, 0, tzinfo=UTC)
    await make_session(
        db, user.discord_id, game.id, e, e + timedelta(seconds=999),
        status=SessionStatus.ERROR,
    )
    # flicker
    f = datetime(2026, 2, 3, 10, 0, tzinfo=UTC)
    await make_session(
        db, user.discord_id, game.id, f, f + timedelta(seconds=999), is_flicker=True
    )
    # soft-deleted
    d = datetime(2026, 2, 4, 10, 0, tzinfo=UTC)
    await make_session(
        db, user.discord_id, game.id, d, d + timedelta(seconds=999),
        deleted_at=datetime.now(UTC),
    )

    resp = await authed_client.get(URL.format(game.id))

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_seconds"] == 600
    assert data["session_count"] == 1


async def test_ignored_game_still_returns_stats(authed_client, db, user):
    game = await make_game(db)
    s = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    await make_session(db, user.discord_id, game.id, s, s + timedelta(seconds=300))
    await make_pref(db, user.discord_id, game.id, is_ignored=True)

    resp = await authed_client.get(URL.format(game.id))

    assert resp.status_code == 200
    assert resp.json()["total_seconds"] == 300


async def test_unknown_game_returns_404(authed_client, db, user):
    resp = await authed_client.get(URL.format(999999))
    assert resp.status_code == 404


async def test_game_with_no_visible_sessions_returns_404(authed_client, db, user):
    # Game exists but caller's only session is soft-deleted
    game = await make_game(db)
    s = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    await make_session(
        db, user.discord_id, game.id, s, s + timedelta(seconds=300),
        deleted_at=datetime.now(UTC),
    )
    resp = await authed_client.get(URL.format(game.id))
    assert resp.status_code == 404


async def test_other_users_sessions_not_counted(authed_client, db, user):
    game = await make_game(db)
    other = await make_user(db, discord_id="222222222222222222", username="other")
    s = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    await make_session(db, other.discord_id, game.id, s, s + timedelta(seconds=300))

    resp = await authed_client.get(URL.format(game.id))
    # Caller has no sessions for this game → 404
    assert resp.status_code == 404
