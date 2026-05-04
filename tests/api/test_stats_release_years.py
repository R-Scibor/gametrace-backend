from datetime import date, datetime, timedelta, timezone

from app.models.session import SessionSource, SessionStatus
from tests.factories import (
    dt,
    make_game,
    make_pref,
    make_session,
)


# ── Empty ─────────────────────────────────────────────────────────────────────

async def test_release_years_empty_user(authed_client):
    resp = await authed_client.get("/api/v1/stats/release-years")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


# ── Aggregation ───────────────────────────────────────────────────────────────

async def test_release_years_single_decade(authed_client, db, user):
    game = await make_game(db)
    game.first_release_date = date(2015, 6, 15)
    db.add(game)
    await db.flush()

    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
    )  # 3600s

    resp = await authed_client.get("/api/v1/stats/release-years")

    assert resp.status_code == 200
    assert resp.json() == {
        "items": [{"decade": "2010s", "total_seconds": 3600}]
    }


async def test_release_years_multiple_decades_sorted_asc(authed_client, db, user):
    years = [1998, 2003, 2011, 2024]
    for i, y in enumerate(years):
        g = await make_game(db, primary_name=f"G{y}")
        g.first_release_date = date(y, 1, 1)
        db.add(g)
        await db.flush()
        await make_session(
            db, user.discord_id, g.id,
            dt(hours_ago=10 + i * 2), dt(hours_ago=9 + i * 2),
        )

    resp = await authed_client.get("/api/v1/stats/release-years")

    assert resp.status_code == 200
    decades = [i["decade"] for i in resp.json()["items"]]
    assert decades == ["1990s", "2000s", "2010s", "2020s"]


async def test_release_years_aggregates_within_decade(authed_client, db, user):
    g1 = await make_game(db, primary_name="G1")
    g1.first_release_date = date(2011, 5, 1)
    g2 = await make_game(db, primary_name="G2")
    g2.first_release_date = date(2018, 9, 9)
    db.add_all([g1, g2])
    await db.flush()

    await make_session(
        db, user.discord_id, g1.id, dt(hours_ago=10), dt(hours_ago=9),
    )  # 3600s
    await make_session(
        db, user.discord_id, g2.id, dt(hours_ago=8), dt(hours_ago=6),
    )  # 7200s

    resp = await authed_client.get("/api/v1/stats/release-years")

    assert resp.status_code == 200
    assert resp.json() == {
        "items": [{"decade": "2010s", "total_seconds": 10800}]
    }


# ── Exclusions ────────────────────────────────────────────────────────────────

async def test_release_years_null_release_date_excluded(authed_client, db, user):
    game = await make_game(db)
    # first_release_date defaults to None
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
    )

    resp = await authed_client.get("/api/v1/stats/release-years")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


async def test_release_years_excludes_error_session(authed_client, db, user):
    game = await make_game(db)
    game.first_release_date = date(2015, 1, 1)
    db.add(game)
    await db.flush()
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=5), dt(hours_ago=4),
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.get("/api/v1/stats/release-years")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


async def test_release_years_excludes_deleted(authed_client, db, user):
    game = await make_game(db)
    game.first_release_date = date(2015, 1, 1)
    db.add(game)
    await db.flush()
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=5), dt(hours_ago=4),
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.get("/api/v1/stats/release-years")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


async def test_release_years_excludes_ignored_game(authed_client, db, user):
    game = await make_game(db)
    game.first_release_date = date(2015, 1, 1)
    db.add(game)
    await db.flush()
    await make_pref(db, user.discord_id, game.id, is_ignored=True)
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
    )

    resp = await authed_client.get("/api/v1/stats/release-years")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


# ── ONGOING included ──────────────────────────────────────────────────────────

async def test_release_years_includes_ongoing(authed_client, db, user):
    game = await make_game(db)
    game.first_release_date = date(2015, 1, 1)
    db.add(game)
    await db.flush()
    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    await make_session(
        db, user.discord_id, game.id, start,
        end_time=None,
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    resp = await authed_client.get("/api/v1/stats/release-years")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["decade"] == "2010s"
    assert 1700 <= items[0]["total_seconds"] <= 1900


# ── Decade boundaries ─────────────────────────────────────────────────────────

async def test_release_years_decade_boundary_2020_is_2020s(authed_client, db, user):
    game = await make_game(db)
    game.first_release_date = date(2020, 1, 1)
    db.add(game)
    await db.flush()
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
    )

    resp = await authed_client.get("/api/v1/stats/release-years")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["decade"] == "2020s"


async def test_release_years_decade_boundary_2019_is_2010s(authed_client, db, user):
    game = await make_game(db)
    game.first_release_date = date(2019, 12, 31)
    db.add(game)
    await db.flush()
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4),
    )

    resp = await authed_client.get("/api/v1/stats/release-years")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["decade"] == "2010s"


# ── Auth ──────────────────────────────────────────────────────────────────────

async def test_release_years_unauthorized(client):
    resp = await client.get(
        "/api/v1/stats/release-years",
        headers={"Authorization": "Bearer badtoken"},
    )

    assert resp.status_code == 401
