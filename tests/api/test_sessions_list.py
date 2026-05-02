from app.models.session import SessionStatus
from tests.factories import dt, make_game, make_session


async def test_list_sessions_excludes_soft_deleted(authed_client, db, user):
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=2), dt(hours_ago=1))
    await make_session(
        db,
        user.discord_id,
        game.id,
        dt(hours_ago=4),
        dt(hours_ago=3),
        deleted_at=dt(hours_ago=1),
    )

    resp = await authed_client.get("/api/v1/sessions")

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1


async def test_list_sessions_status_filter_recents(authed_client, db, user):
    """Dashboard recents query: COMPLETED + ERROR only, ONGOING excluded."""
    game = await make_game(db)
    completed = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=2), dt(hours_ago=1)
    )
    errored = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=4), status=SessionStatus.ERROR
    )
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=1), status=SessionStatus.ONGOING
    )

    resp = await authed_client.get(
        "/api/v1/sessions?status=COMPLETED&status=ERROR&limit=5"
    )

    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()}
    assert ids == {completed.id, errored.id}


async def test_list_sessions_ordered_desc_and_paginated(authed_client, db, user):
    game = await make_game(db)
    for hours in (5, 4, 3, 2, 1):
        await make_session(
            db, user.discord_id, game.id, dt(hours_ago=hours), dt(hours_ago=hours - 0.5)
        )

    resp = await authed_client.get("/api/v1/sessions?limit=3")

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 3
    starts = [row["start_time"] for row in rows]
    assert starts == sorted(starts, reverse=True)


async def test_list_sessions_includes_game_brief(authed_client, db, user):
    game = await make_game(db, primary_name="Halo")
    game.cover_image_url = "https://example.com/halo.jpg"
    await db.flush()
    await make_session(db, user.discord_id, game.id, dt(hours_ago=2), dt(hours_ago=1))

    resp = await authed_client.get("/api/v1/sessions")

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["game"]["primary_name"] == "Halo"
    assert row["game"]["cover_image_url"] == "https://example.com/halo.jpg"
