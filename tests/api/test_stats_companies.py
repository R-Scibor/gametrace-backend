from datetime import datetime, timedelta, timezone

from app.models.session import SessionSource, SessionStatus
from tests.factories import (
    dt,
    make_game,
    make_pref,
    make_session,
)


# ── Param validation ──────────────────────────────────────────────────────────

async def test_companies_missing_role_returns_422(authed_client):
    resp = await authed_client.get("/api/v1/stats/companies")
    assert resp.status_code == 422


async def test_companies_invalid_role_returns_422(authed_client):
    resp = await authed_client.get("/api/v1/stats/companies?role=studio")
    assert resp.status_code == 422


async def test_companies_invalid_limit(authed_client):
    resp = await authed_client.get(
        "/api/v1/stats/companies?role=developer&limit=0"
    )
    assert resp.status_code == 422

    resp = await authed_client.get(
        "/api/v1/stats/companies?role=developer&limit=51"
    )
    assert resp.status_code == 422


# ── Empty ─────────────────────────────────────────────────────────────────────

async def test_companies_empty_user_developer(authed_client):
    resp = await authed_client.get("/api/v1/stats/companies?role=developer")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


# ── Aggregation ───────────────────────────────────────────────────────────────

async def test_companies_developer_leaderboard(authed_client, db, user):
    g1 = await make_game(db, primary_name="Game1")
    g1.developers = ["Acme", "Repeat"]
    g2 = await make_game(db, primary_name="Game2")
    g2.developers = ["Repeat"]
    g3 = await make_game(db, primary_name="Game3")
    g3.developers = ["Solo"]
    db.add_all([g1, g2, g3])
    await db.flush()

    await make_session(
        db, user.discord_id, g1.id, dt(hours_ago=10), dt(hours_ago=9),
    )  # 3600s
    await make_session(
        db, user.discord_id, g2.id, dt(hours_ago=8), dt(hours_ago=6),
    )  # 7200s
    await make_session(
        db, user.discord_id, g3.id, dt(hours_ago=5), dt(hours_ago=4),
    )  # 3600s

    resp = await authed_client.get("/api/v1/stats/companies?role=developer")

    assert resp.status_code == 200
    items = resp.json()["items"]
    by_name = {i["name"]: i for i in items}
    # Repeat: g1 (3600) + g2 (7200) = 10800, 2 games
    assert by_name["Repeat"]["total_seconds"] == 10800
    assert by_name["Repeat"]["game_count"] == 2
    # Acme: g1 only = 3600, 1 game
    assert by_name["Acme"]["total_seconds"] == 3600
    assert by_name["Acme"]["game_count"] == 1
    # Solo: g3 only = 3600, 1 game
    assert by_name["Solo"]["total_seconds"] == 3600
    assert by_name["Solo"]["game_count"] == 1


async def test_companies_publisher_filter(authed_client, db, user):
    game = await make_game(db)
    game.developers = ["A"]
    game.publishers = ["B"]
    db.add(game)
    await db.flush()

    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
    )  # 3600s

    resp_pub = await authed_client.get(
        "/api/v1/stats/companies?role=publisher"
    )
    assert resp_pub.status_code == 200
    assert resp_pub.json() == {
        "items": [{"name": "B", "total_seconds": 3600, "game_count": 1}]
    }

    resp_dev = await authed_client.get(
        "/api/v1/stats/companies?role=developer"
    )
    assert resp_dev.status_code == 200
    assert resp_dev.json() == {
        "items": [{"name": "A", "total_seconds": 3600, "game_count": 1}]
    }


async def test_companies_dual_role_company(authed_client, db, user):
    game = await make_game(db)
    game.developers = ["From"]
    game.publishers = ["From"]
    db.add(game)
    await db.flush()

    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
    )  # 3600s

    resp_dev = await authed_client.get(
        "/api/v1/stats/companies?role=developer"
    )
    resp_pub = await authed_client.get(
        "/api/v1/stats/companies?role=publisher"
    )

    expected = {"items": [{"name": "From", "total_seconds": 3600, "game_count": 1}]}
    assert resp_dev.json() == expected
    assert resp_pub.json() == expected


# ── Limit ─────────────────────────────────────────────────────────────────────

async def test_companies_limit_honored(authed_client, db, user):
    for i in range(5):
        g = await make_game(db, primary_name=f"G{i}")
        g.developers = [f"Dev{i}"]
        db.add(g)
        await db.flush()
        await make_session(
            db, user.discord_id, g.id,
            dt(hours_ago=10 + i * 2), dt(hours_ago=9 + i * 2),
        )

    resp = await authed_client.get(
        "/api/v1/stats/companies?role=developer&limit=2"
    )

    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2


async def test_companies_default_limit_is_10(authed_client, db, user):
    for i in range(12):
        g = await make_game(db, primary_name=f"G{i:02d}")
        g.developers = [f"Dev{i:02d}"]
        db.add(g)
        await db.flush()
        await make_session(
            db, user.discord_id, g.id,
            dt(hours_ago=50 + i * 2), dt(hours_ago=49 + i * 2),
        )

    resp = await authed_client.get("/api/v1/stats/companies?role=developer")

    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 10


# ── Tie-break ─────────────────────────────────────────────────────────────────

async def test_companies_tie_break_by_name_asc(authed_client, db, user):
    g1 = await make_game(db, primary_name="Game1")
    g1.developers = ["Zebra"]
    g2 = await make_game(db, primary_name="Game2")
    g2.developers = ["Alpha"]
    db.add_all([g1, g2])
    await db.flush()

    await make_session(
        db, user.discord_id, g1.id, dt(hours_ago=5), dt(hours_ago=4),
    )  # 3600s
    await make_session(
        db, user.discord_id, g2.id, dt(hours_ago=10), dt(hours_ago=9),
    )  # 3600s — same total

    resp = await authed_client.get("/api/v1/stats/companies?role=developer")

    assert resp.status_code == 200
    names = [i["name"] for i in resp.json()["items"]]
    assert names == ["Alpha", "Zebra"]


# ── Exclusions ────────────────────────────────────────────────────────────────

async def test_companies_excludes_error_session(authed_client, db, user):
    game = await make_game(db)
    game.developers = ["X"]
    db.add(game)
    await db.flush()
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=5), dt(hours_ago=4),
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.get("/api/v1/stats/companies?role=developer")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


async def test_companies_excludes_deleted(authed_client, db, user):
    game = await make_game(db)
    game.developers = ["X"]
    db.add(game)
    await db.flush()
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=5), dt(hours_ago=4),
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.get("/api/v1/stats/companies?role=developer")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


async def test_companies_excludes_ignored_game(authed_client, db, user):
    game = await make_game(db)
    game.developers = ["X"]
    db.add(game)
    await db.flush()
    await make_pref(db, user.discord_id, game.id, is_ignored=True)
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
    )

    resp = await authed_client.get("/api/v1/stats/companies?role=developer")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


# ── ONGOING included ──────────────────────────────────────────────────────────

async def test_companies_includes_ongoing(authed_client, db, user):
    game = await make_game(db)
    game.developers = ["LiveDev"]
    db.add(game)
    await db.flush()
    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    await make_session(
        db, user.discord_id, game.id, start,
        end_time=None,
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    resp = await authed_client.get("/api/v1/stats/companies?role=developer")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "LiveDev"
    assert 1700 <= items[0]["total_seconds"] <= 1900
    assert items[0]["game_count"] == 1


# ── Auth ──────────────────────────────────────────────────────────────────────

async def test_companies_unauthorized(client):
    resp = await client.get(
        "/api/v1/stats/companies?role=developer",
        headers={"Authorization": "Bearer badtoken"},
    )

    assert resp.status_code == 401
