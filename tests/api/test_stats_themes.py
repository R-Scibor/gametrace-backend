from tests.factories import (
    dt,
    make_game,
    make_session,
)


async def test_themes_empty_user(authed_client):
    resp = await authed_client.get("/api/v1/stats/themes")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


async def test_themes_single_game_single_theme(authed_client, db, user):
    game = await make_game(db)
    game.themes = ["Fantasy"]
    db.add(game)
    await db.flush()
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=5), dt(hours_ago=4),  # 3600s
    )

    resp = await authed_client.get("/api/v1/stats/themes")

    assert resp.status_code == 200
    assert resp.json() == {"items": [{"theme": "Fantasy", "total_seconds": 3600}]}


async def test_themes_multi_theme_game(authed_client, db, user):
    game = await make_game(db)
    game.themes = ["Fantasy", "Open World"]
    db.add(game)
    await db.flush()
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=5), dt(hours_ago=4),  # 3600s
    )

    resp = await authed_client.get("/api/v1/stats/themes")

    assert resp.status_code == 200
    by_theme = {i["theme"]: i["total_seconds"] for i in resp.json()["items"]}
    assert by_theme == {"Fantasy": 3600, "Open World": 3600}


async def test_themes_sorted_desc(authed_client, db, user):
    g1 = await make_game(db, primary_name="A")
    g1.themes = ["Small"]
    g2 = await make_game(db, primary_name="B")
    g2.themes = ["Big"]
    db.add_all([g1, g2])
    await db.flush()

    await make_session(
        db, user.discord_id, g1.id, dt(hours_ago=20), dt(hours_ago=19),
    )  # 3600s
    await make_session(
        db, user.discord_id, g2.id, dt(hours_ago=15), dt(hours_ago=10),
    )  # 18000s

    resp = await authed_client.get("/api/v1/stats/themes")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["theme"] for i in items] == ["Big", "Small"]
    assert items[0]["total_seconds"] >= items[1]["total_seconds"]


async def test_themes_unauthorized(client):
    resp = await client.get(
        "/api/v1/stats/themes", headers={"Authorization": "Bearer badtoken"}
    )

    assert resp.status_code == 401
