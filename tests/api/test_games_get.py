from datetime import UTC, datetime, timedelta

from tests.factories import make_game, make_pref, make_session, make_user

URL = "/api/v1/games/{}"


async def test_returns_game_shape_and_playtime(authed_client, db, user):
    game = await make_game(db, primary_name="Hollow Knight")
    s1 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    await make_session(db, user.discord_id, game.id, s1, s1 + timedelta(seconds=3600))
    s2 = datetime(2026, 3, 5, 20, 0, tzinfo=UTC)
    await make_session(db, user.discord_id, game.id, s2, s2 + timedelta(seconds=1800))

    resp = await authed_client.get(URL.format(game.id))

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == game.id
    assert data["primary_name"] == "Hollow Knight"
    assert data["total_seconds"] == 5400
    # last_played = max(start_time), matching the library card
    assert data["last_played"].startswith("2026-03-05")
    assert data["is_ignored"] is False
    assert data["is_accepted"] is None
    # response carries the full list-item contract
    assert "cover_source" in data
    assert "enrichment_status" in data


async def test_ignored_game_still_resolves_by_id(authed_client, db, user):
    game = await make_game(db)
    s = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    await make_session(db, user.discord_id, game.id, s, s + timedelta(seconds=300))
    await make_pref(db, user.discord_id, game.id, is_ignored=True)

    resp = await authed_client.get(URL.format(game.id))

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_ignored"] is True
    assert data["total_seconds"] == 300


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


async def test_other_users_game_not_accessible(authed_client, db, user):
    game = await make_game(db)
    other = await make_user(db, discord_id="222222222222222222", username="other")
    s = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    await make_session(db, other.discord_id, game.id, s, s + timedelta(seconds=300))

    resp = await authed_client.get(URL.format(game.id))
    # Caller has no sessions for this game → 404
    assert resp.status_code == 404
