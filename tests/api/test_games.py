from datetime import timezone

from app.models.game import EnrichmentStatus

from tests.factories import (
    dt,
    make_game,
    make_pref,
    make_session,
    make_user,
)


# ── GET /games ────────────────────────────────────────────────────────────────

async def test_returns_user_games(authed_client, db, user):
    game_a = await make_game(db, "Alpha")
    game_b = await make_game(db, "Beta")
    await make_session(db, user.discord_id, game_a.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, game_b.id, dt(hours_ago=5), dt(hours_ago=4))

    resp = await authed_client.get("/api/v1/games")

    assert resp.status_code == 200
    names = {g["primary_name"] for g in resp.json()}
    assert names == {"Alpha", "Beta"}


async def test_excludes_other_users_games(authed_client, db, user):
    other = await make_user(db, discord_id="222222222222222222", username="other")
    game_mine = await make_game(db, "Mine")
    game_theirs = await make_game(db, "Theirs")
    await make_session(db, user.discord_id, game_mine.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, other.discord_id, game_theirs.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games")

    assert resp.status_code == 200
    names = [g["primary_name"] for g in resp.json()]
    assert "Mine" in names
    assert "Theirs" not in names


async def test_excludes_ignored_games(authed_client, db, user):
    game_ok = await make_game(db, "Visible")
    game_hidden = await make_game(db, "Hidden")
    await make_session(db, user.discord_id, game_ok.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, game_hidden.id, dt(hours_ago=5), dt(hours_ago=4))
    await make_pref(db, user.discord_id, game_hidden.id, is_ignored=True)

    resp = await authed_client.get("/api/v1/games")

    assert resp.status_code == 200
    names = [g["primary_name"] for g in resp.json()]
    assert "Visible" in names
    assert "Hidden" not in names


async def test_status_filter_needs_review(authed_client, db, user):
    game_pending = await make_game(db, "Pending Game", EnrichmentStatus.PENDING)
    game_review = await make_game(db, "Review Game", EnrichmentStatus.NEEDS_REVIEW)
    await make_session(db, user.discord_id, game_pending.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, game_review.id, dt(hours_ago=5), dt(hours_ago=4))

    resp = await authed_client.get("/api/v1/games?status=NEEDS_REVIEW")

    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["primary_name"] == "Review Game"


async def test_pagination(authed_client, db, user):
    for i in range(25):
        game = await make_game(db, f"Game {i:02d}")
        await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games?skip=20&limit=10")

    assert resp.status_code == 200
    assert len(resp.json()) == 5


async def test_game_with_no_sessions_not_returned(authed_client, db, user):
    _orphan = await make_game(db, "Orphan Game")

    resp = await authed_client.get("/api/v1/games")

    assert resp.status_code == 200
    names = [g["primary_name"] for g in resp.json()]
    assert "Orphan Game" not in names


# ── GET /games/{id}/sessions ──────────────────────────────────────────────────

async def test_returns_sessions_for_game(authed_client, db, user):
    game = await make_game(db)
    s1 = await make_session(db, user.discord_id, game.id, dt(hours_ago=10), dt(hours_ago=9))
    s2 = await make_session(db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4))
    s3 = await make_session(db, user.discord_id, game.id, dt(hours_ago=2), dt(hours_ago=1))

    resp = await authed_client.get(f"/api/v1/games/{game.id}/sessions")

    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert ids == [s3.id, s2.id, s1.id]  # newest first


async def test_ignored_game_returns_empty_list(authed_client, db, user):
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_pref(db, user.discord_id, game.id, is_ignored=True)

    resp = await authed_client.get(f"/api/v1/games/{game.id}/sessions")

    assert resp.status_code == 200
    assert resp.json() == []


async def test_excludes_soft_deleted_sessions(authed_client, db, user):
    from datetime import datetime
    game = await make_game(db)
    visible = await make_session(db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4))
    deleted = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2),
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.get(f"/api/v1/games/{game.id}/sessions")

    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert visible.id in ids
    assert deleted.id not in ids
