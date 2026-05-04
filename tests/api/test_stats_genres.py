from datetime import datetime, timedelta, timezone

from app.models.session import SessionSource, SessionStatus
from tests.factories import (
    dt,
    make_game,
    make_pref,
    make_session,
)


# ── Empty ─────────────────────────────────────────────────────────────────────

async def test_genres_empty_user(authed_client):
    resp = await authed_client.get("/api/v1/stats/genres")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


# ── Basic aggregation ─────────────────────────────────────────────────────────

async def test_genres_single_game_single_genre(authed_client, db, user):
    game = await make_game(db)
    game.genres = ["RPG"]
    db.add(game)
    await db.flush()
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=5), dt(hours_ago=4),  # 3600s
    )

    resp = await authed_client.get("/api/v1/stats/genres")

    assert resp.status_code == 200
    assert resp.json() == {"items": [{"genre": "RPG", "total_seconds": 3600}]}


async def test_genres_multi_genre_game(authed_client, db, user):
    game = await make_game(db)
    game.genres = ["RPG", "Adventure"]
    db.add(game)
    await db.flush()
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=5), dt(hours_ago=4),  # 3600s
    )

    resp = await authed_client.get("/api/v1/stats/genres")

    assert resp.status_code == 200
    items = resp.json()["items"]
    by_genre = {i["genre"]: i["total_seconds"] for i in items}
    assert by_genre == {"RPG": 3600, "Adventure": 3600}


async def test_genres_multiple_games_aggregated(authed_client, db, user):
    g1 = await make_game(db, primary_name="Game1")
    g1.genres = ["RPG"]
    g2 = await make_game(db, primary_name="Game2")
    g2.genres = ["RPG", "Strategy"]
    db.add_all([g1, g2])
    await db.flush()

    await make_session(
        db, user.discord_id, g1.id,
        dt(hours_ago=10), dt(hours_ago=9),  # 3600s
    )
    await make_session(
        db, user.discord_id, g2.id,
        dt(hours_ago=8), dt(hours_ago=6),  # 7200s
    )

    resp = await authed_client.get("/api/v1/stats/genres")

    assert resp.status_code == 200
    by_genre = {i["genre"]: i["total_seconds"] for i in resp.json()["items"]}
    assert by_genre == {"RPG": 10800, "Strategy": 7200}


async def test_genres_sorted_desc(authed_client, db, user):
    g1 = await make_game(db, primary_name="A")
    g1.genres = ["Small"]
    g2 = await make_game(db, primary_name="B")
    g2.genres = ["Big"]
    g3 = await make_game(db, primary_name="C")
    g3.genres = ["Mid"]
    db.add_all([g1, g2, g3])
    await db.flush()

    await make_session(
        db, user.discord_id, g1.id, dt(hours_ago=20), dt(hours_ago=19, hours_from_now=0),
    )  # 3600s
    await make_session(
        db, user.discord_id, g2.id, dt(hours_ago=15), dt(hours_ago=10),
    )  # 18000s
    await make_session(
        db, user.discord_id, g3.id, dt(hours_ago=8), dt(hours_ago=6),
    )  # 7200s

    resp = await authed_client.get("/api/v1/stats/genres")

    assert resp.status_code == 200
    seconds = [i["total_seconds"] for i in resp.json()["items"]]
    assert seconds == sorted(seconds, reverse=True)
    assert [i["genre"] for i in resp.json()["items"]] == ["Big", "Mid", "Small"]


# ── Exclusions ────────────────────────────────────────────────────────────────

async def test_genres_excludes_error_session(authed_client, db, user):
    game = await make_game(db)
    game.genres = ["RPG"]
    db.add(game)
    await db.flush()
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=5), dt(hours_ago=4),
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.get("/api/v1/stats/genres")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


async def test_genres_excludes_deleted(authed_client, db, user):
    game = await make_game(db)
    game.genres = ["RPG"]
    db.add(game)
    await db.flush()
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=5), dt(hours_ago=4),
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.get("/api/v1/stats/genres")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


async def test_genres_excludes_ignored_game(authed_client, db, user):
    game = await make_game(db)
    game.genres = ["RPG"]
    db.add(game)
    await db.flush()
    await make_pref(db, user.discord_id, game.id, is_ignored=True)
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
    )

    resp = await authed_client.get("/api/v1/stats/genres")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


# ── ONGOING included ──────────────────────────────────────────────────────────

async def test_genres_includes_ongoing(authed_client, db, user):
    game = await make_game(db)
    game.genres = ["RPG"]
    db.add(game)
    await db.flush()
    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    await make_session(
        db, user.discord_id, game.id, start,
        end_time=None,
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    resp = await authed_client.get("/api/v1/stats/genres")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["genre"] == "RPG"
    assert 1700 <= items[0]["total_seconds"] <= 1900


# ── Empty array ───────────────────────────────────────────────────────────────

async def test_genres_empty_genres_list_dropped(authed_client, db, user):
    game = await make_game(db)
    # genres defaults to [] via server_default
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
    )

    resp = await authed_client.get("/api/v1/stats/genres")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


# ── Auth ──────────────────────────────────────────────────────────────────────

async def test_genres_unauthorized(client):
    resp = await client.get(
        "/api/v1/stats/genres", headers={"Authorization": "Bearer badtoken"}
    )

    assert resp.status_code == 401
