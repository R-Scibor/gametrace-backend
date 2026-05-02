from app.models.session import SessionStatus
from tests.factories import dt, make_game, make_pref, make_session


async def test_dashboard_totals_basic(authed_client, db, user):
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))  # 3600s

    resp = await authed_client.get("/api/v1/stats/dashboard")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_seconds_7d"] == 3600
    assert data["total_seconds_30d"] == 3600


async def test_dashboard_excludes_ignored_game(authed_client, db, user):
    ignored_game = await make_game(db, primary_name="Ignored")
    normal_game = await make_game(db, primary_name="Normal")
    await make_pref(db, user.discord_id, ignored_game.id, is_ignored=True)

    # Both sessions in last 7 days, equal duration
    await make_session(db, user.discord_id, ignored_game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, normal_game.id, dt(hours_ago=5), dt(hours_ago=4))

    resp = await authed_client.get("/api/v1/stats/dashboard")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_seconds_7d"] == 3600  # only normal_game
    assert data["total_seconds_30d"] == 3600


async def test_dashboard_pending_errors_not_filtered_by_is_ignored(
    authed_client, db, user
):
    """ERROR sessions always show, even for ignored games — user must resolve them."""
    ignored_game = await make_game(db, primary_name="Ignored")
    await make_pref(db, user.discord_id, ignored_game.id, is_ignored=True)
    await make_session(
        db,
        user.discord_id,
        ignored_game.id,
        dt(hours_ago=3),
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.get("/api/v1/stats/dashboard")

    assert resp.status_code == 200
    assert len(resp.json()["pending_errors"]) == 1


async def test_dashboard_active_session_includes_game_id_and_cover(
    authed_client, db, user
):
    game = await make_game(db, primary_name="Active")
    game.cover_image_url = "https://example.com/cover.jpg"
    await db.flush()
    await make_session(
        db,
        user.discord_id,
        game.id,
        dt(hours_ago=1),
        status=SessionStatus.ONGOING,
    )

    resp = await authed_client.get("/api/v1/stats/dashboard")

    assert resp.status_code == 200
    active = resp.json()["active_session"]
    assert active is not None
    assert active["game_id"] == game.id
    assert active["cover_image_url"] == "https://example.com/cover.jpg"
    assert active["game_name"] == "Active"


async def test_dashboard_total_seconds_today_field_present(authed_client, db, user):
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=2), dt(hours_ago=1))

    resp = await authed_client.get("/api/v1/stats/dashboard")

    assert resp.status_code == 200
    data = resp.json()
    assert "total_seconds_today" in data
    # Session in the last 2h is always after local midnight regardless of tz.
    assert data["total_seconds_today"] == 3600
