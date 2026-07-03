from sqlalchemy import select

from app.models.game import Game, UserGamePreference

from tests.factories import (
    dt,
    make_alias,
    make_game,
    make_pref,
    make_session,
)


# ── Auth gate ─────────────────────────────────────────────────────────────────

async def test_old_public_merge_url_gone(authed_client, db, user):
    source = await make_game(db, "Source Game")
    target = await make_game(db, "Target Game")

    resp = await authed_client.post(f"/api/v1/games/{source.id}/merge/{target.id}")

    assert resp.status_code == 404


async def test_non_admin_on_admin_merge_url_returns_403(authed_client, db, user):
    source = await make_game(db, "Source Game")
    target = await make_game(db, "Target Game")

    resp = await authed_client.post(f"/api/v1/admin/games/{source.id}/merge/{target.id}")

    assert resp.status_code == 403


async def test_unauthenticated_on_admin_merge_url_returns_401(client, db):
    source = await make_game(db, "Source Game")
    target = await make_game(db, "Target Game")

    resp = await client.post(
        f"/api/v1/admin/games/{source.id}/merge/{target.id}",
        headers={"Authorization": "Bearer badtoken"},
    )

    assert resp.status_code == 401


# ── POST /admin/games/{id}/merge/{target_id} ─────────────────────────────────

async def test_merge_happy_path(admin_client, db, admin_user):
    source = await make_game(db, "Source Game")
    target = await make_game(db, "Target Game")
    s = await make_session(db, admin_user.discord_id, source.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await admin_client.post(f"/api/v1/admin/games/{source.id}/merge/{target.id}")

    assert resp.status_code == 204
    deleted = await db.get(Game, source.id)
    assert deleted is None
    await db.refresh(s)
    assert s.game_id == target.id


async def test_aliases_reassigned(admin_client, db, admin_user):
    source = await make_game(db, "Source")
    target = await make_game(db, "Target")
    alias = await make_alias(db, source.id, "source.exe")

    await admin_client.post(f"/api/v1/admin/games/{source.id}/merge/{target.id}")

    await db.refresh(alias)
    assert alias.game_id == target.id


async def test_user_preference_conflict_resolved(admin_client, db, admin_user):
    """User has a pref for both games — source pref is dropped, no UNIQUE violation."""
    source = await make_game(db, "Source")
    target = await make_game(db, "Target")
    await make_pref(db, admin_user.discord_id, source.id, is_ignored=True)
    await make_pref(db, admin_user.discord_id, target.id, is_ignored=False)

    resp = await admin_client.post(f"/api/v1/admin/games/{source.id}/merge/{target.id}")

    assert resp.status_code == 204
    result = await db.execute(
        select(UserGamePreference).where(UserGamePreference.game_id == target.id)
    )
    assert len(result.scalars().all()) == 1


async def test_merge_self_returns_400(admin_client, db, admin_user):
    game = await make_game(db)

    resp = await admin_client.post(f"/api/v1/admin/games/{game.id}/merge/{game.id}")

    assert resp.status_code == 400


async def test_merge_source_not_found(admin_client, db, admin_user):
    target = await make_game(db)

    resp = await admin_client.post(f"/api/v1/admin/games/99999/merge/{target.id}")

    assert resp.status_code == 404


async def test_merge_target_not_found(admin_client, db, admin_user):
    source = await make_game(db)

    resp = await admin_client.post(f"/api/v1/admin/games/{source.id}/merge/99999")

    assert resp.status_code == 404
