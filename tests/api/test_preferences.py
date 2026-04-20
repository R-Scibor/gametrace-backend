from sqlalchemy import select

from app.models.game import UserGamePreference
from tests.factories import make_game, make_pref


async def test_upsert_creates_preference(authed_client, db, user):
    game = await make_game(db)

    resp = await authed_client.put(
        f"/api/v1/user/preferences/{game.id}",
        json={"is_ignored": True, "custom_tag": "Retro"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"game_id": game.id, "is_ignored": True, "custom_tag": "Retro"}

    row = (
        await db.execute(
            select(UserGamePreference).where(
                UserGamePreference.user_id == user.discord_id,
                UserGamePreference.game_id == game.id,
            )
        )
    ).scalar_one()
    assert row.is_ignored is True
    assert row.custom_tag == "Retro"


async def test_upsert_updates_existing_preference(authed_client, db, user):
    game = await make_game(db)
    await make_pref(db, user.discord_id, game.id, is_ignored=True)

    resp = await authed_client.put(
        f"/api/v1/user/preferences/{game.id}",
        json={"is_ignored": False, "custom_tag": "Favourite"},
    )

    assert resp.status_code == 200
    assert resp.json()["is_ignored"] is False

    count = (
        await db.execute(
            select(UserGamePreference).where(
                UserGamePreference.user_id == user.discord_id,
                UserGamePreference.game_id == game.id,
            )
        )
    ).all()
    assert len(count) == 1  # upsert, not duplicate insert


async def test_delete_removes_preference(authed_client, db, user):
    game = await make_game(db)
    await make_pref(db, user.discord_id, game.id, is_ignored=True)

    resp = await authed_client.delete(f"/api/v1/user/preferences/{game.id}")

    assert resp.status_code == 204
    remaining = (
        await db.execute(
            select(UserGamePreference).where(
                UserGamePreference.user_id == user.discord_id,
                UserGamePreference.game_id == game.id,
            )
        )
    ).scalar_one_or_none()
    assert remaining is None


async def test_delete_nonexistent_preference_is_idempotent(authed_client, db):
    game = await make_game(db)

    resp = await authed_client.delete(f"/api/v1/user/preferences/{game.id}")

    assert resp.status_code == 204


async def test_put_nonexistent_game_returns_404(authed_client):
    resp = await authed_client.put(
        "/api/v1/user/preferences/99999",
        json={"is_ignored": True},
    )

    assert resp.status_code == 404


async def test_put_requires_auth(client, db):
    game = await make_game(db)

    resp = await client.put(
        f"/api/v1/user/preferences/{game.id}",
        json={"is_ignored": True},
    )

    assert resp.status_code == 403  # HTTPBearer raises 403 when no creds


async def test_put_ignored_hides_game_from_summary(authed_client, db, user):
    """End-to-end: flipping is_ignored=true via PUT removes the game from /stats/summary."""
    from tests.factories import dt, make_session

    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    # Baseline: game counted
    first = await authed_client.get("/api/v1/stats/summary")
    assert first.json()["total_seconds"] == 3600

    # Flip preference
    put_resp = await authed_client.put(
        f"/api/v1/user/preferences/{game.id}",
        json={"is_ignored": True},
    )
    assert put_resp.status_code == 200

    # Now excluded
    second = await authed_client.get("/api/v1/stats/summary")
    assert second.json()["total_seconds"] == 0
