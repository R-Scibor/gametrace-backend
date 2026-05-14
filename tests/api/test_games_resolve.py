from datetime import datetime, timezone

from app.models.session import SessionStatus
from tests.factories import (
    dt,
    make_alias,
    make_game,
    make_pref,
    make_session,
    make_user,
)


# ── happy paths ───────────────────────────────────────────────────────────────

async def test_exact_match_on_primary_name(authed_client, db, user):
    game = await make_game(db, "Valorant")
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games/resolve", params={"name": "Valorant"})

    assert resp.status_code == 200
    assert resp.json() == {"game_id": game.id, "name": "Valorant"}


async def test_case_insensitive_primary_name(authed_client, db, user):
    game = await make_game(db, "Valorant")
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games/resolve", params={"name": "valorant"})

    assert resp.status_code == 200
    assert resp.json() == {"game_id": game.id, "name": "Valorant"}


async def test_match_via_alias(authed_client, db, user):
    game = await make_game(db, "Red Dead Redemption 2")
    await make_alias(db, game.id, "RDR2.exe")
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games/resolve", params={"name": "rdr2.exe"})

    assert resp.status_code == 200
    assert resp.json() == {"game_id": game.id, "name": "Red Dead Redemption 2"}


async def test_error_session_still_counts(authed_client, db, user):
    """ERROR sessions = the user played the game, resolution should succeed."""
    game = await make_game(db, "Skyrim")
    await make_session(
        db,
        user.discord_id,
        game.id,
        dt(hours_ago=5),
        end_time=None,
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.get("/api/v1/games/resolve", params={"name": "Skyrim"})

    assert resp.status_code == 200
    assert resp.json() == {"game_id": game.id, "name": "Skyrim"}


async def test_ignored_game_still_resolves(authed_client, db, user):
    """is_ignored is a display filter, not a 'does not exist' signal."""
    game = await make_game(db, "Minesweeper")
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_pref(db, user.discord_id, game.id, is_ignored=True)

    resp = await authed_client.get("/api/v1/games/resolve", params={"name": "Minesweeper"})

    assert resp.status_code == 200
    assert resp.json() == {"game_id": game.id, "name": "Minesweeper"}


# ── miss paths ────────────────────────────────────────────────────────────────

async def test_no_match_returns_null(authed_client, db, user):
    game = await make_game(db, "Valorant")
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games/resolve", params={"name": "Overwatch"})

    assert resp.status_code == 200
    assert resp.json() is None


async def test_other_users_game_does_not_leak(authed_client, db, user):
    other = await make_user(db, discord_id="222222222222222222", username="other")
    game = await make_game(db, "Theirs")
    await make_session(db, other.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games/resolve", params={"name": "Theirs"})

    assert resp.status_code == 200
    assert resp.json() is None


async def test_only_soft_deleted_sessions_does_not_resolve(authed_client, db, user):
    """User removed the game from their library — should not resolve."""
    game = await make_game(db, "Forgotten")
    await make_session(
        db,
        user.discord_id,
        game.id,
        dt(hours_ago=3),
        dt(hours_ago=2),
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.get("/api/v1/games/resolve", params={"name": "Forgotten"})

    assert resp.status_code == 200
    assert resp.json() is None


async def test_alias_for_game_user_never_played_does_not_resolve(authed_client, db, user):
    """Alias exists globally but the user has no sessions for that game."""
    game = await make_game(db, "Stranger Game")
    await make_alias(db, game.id, "stranger.exe")
    # no session for `user`

    resp = await authed_client.get("/api/v1/games/resolve", params={"name": "stranger.exe"})

    assert resp.status_code == 200
    assert resp.json() is None


# ── auth + validation ─────────────────────────────────────────────────────────

async def test_requires_auth(client, db, user):
    """`client` is the unauthenticated httpx fixture."""
    resp = await client.get("/api/v1/games/resolve", params={"name": "anything"})
    assert resp.status_code == 401


async def test_missing_name_param_is_422(authed_client):
    resp = await authed_client.get("/api/v1/games/resolve")
    assert resp.status_code == 422


async def test_empty_name_param_is_422(authed_client):
    resp = await authed_client.get("/api/v1/games/resolve", params={"name": ""})
    assert resp.status_code == 422
