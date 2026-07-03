import base64

from sqlalchemy import select

from app.models.game import CoverSource, Game, UserGamePreference

from tests.factories import (
    dt,
    make_alias,
    make_game,
    make_pref,
    make_session,
)

TINY_IMAGE_B64 = base64.b64encode(b"fake_image_bytes").decode()


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


async def test_old_public_cover_url_gone(authed_client, db, user):
    game = await make_game(db, "Source Game")

    resp = await authed_client.put(
        f"/api/v1/games/{game.id}/cover",
        json={"image_base64": TINY_IMAGE_B64, "extension": "jpg"},
    )

    assert resp.status_code == 404


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


# ── PUT /admin/games/{id}/cover ───────────────────────────────────────────────

async def test_cover_upload_happy_path(admin_client, db, admin_user, tmp_path, monkeypatch):
    monkeypatch.setenv("COVERS_DIR", str(tmp_path))
    game = await make_game(db, "Cover Game")

    resp = await admin_client.put(
        f"/api/v1/admin/games/{game.id}/cover",
        json={"image_base64": TINY_IMAGE_B64, "extension": "jpg"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["cover_image_url"] == f"/covers/{game.id}.jpg"
    assert body["cover_source"] == "CUSTOM"

    written = tmp_path / f"{game.id}.jpg"
    assert written.exists()
    assert written.read_bytes() == base64.b64decode(TINY_IMAGE_B64)

    await db.refresh(game)
    assert game.cover_image_url == f"/covers/{game.id}.jpg"
    assert game.cover_source == CoverSource.CUSTOM


async def test_cover_upload_non_admin_returns_403(authed_client, db, user, tmp_path, monkeypatch):
    monkeypatch.setenv("COVERS_DIR", str(tmp_path))
    game = await make_game(db)

    resp = await authed_client.put(
        f"/api/v1/admin/games/{game.id}/cover",
        json={"image_base64": TINY_IMAGE_B64, "extension": "jpg"},
    )

    assert resp.status_code == 403
    assert list(tmp_path.iterdir()) == []


async def test_cover_upload_missing_game_returns_404(admin_client, db, admin_user, tmp_path, monkeypatch):
    monkeypatch.setenv("COVERS_DIR", str(tmp_path))

    resp = await admin_client.put(
        "/api/v1/admin/games/99999/cover",
        json={"image_base64": TINY_IMAGE_B64, "extension": "jpg"},
    )

    assert resp.status_code == 404


async def test_cover_upload_path_traversal_extension_returns_422(admin_client, db, admin_user, tmp_path, monkeypatch):
    monkeypatch.setenv("COVERS_DIR", str(tmp_path))
    game = await make_game(db)

    resp = await admin_client.put(
        f"/api/v1/admin/games/{game.id}/cover",
        json={"image_base64": TINY_IMAGE_B64, "extension": "../../etc/x"},
    )

    assert resp.status_code == 422
    assert list(tmp_path.iterdir()) == []


async def test_cover_upload_disallowed_extension_returns_422(admin_client, db, admin_user, tmp_path, monkeypatch):
    monkeypatch.setenv("COVERS_DIR", str(tmp_path))
    game = await make_game(db)

    resp = await admin_client.put(
        f"/api/v1/admin/games/{game.id}/cover",
        json={"image_base64": TINY_IMAGE_B64, "extension": "svg"},
    )

    assert resp.status_code == 422


async def test_cover_upload_malformed_base64_returns_422(admin_client, db, admin_user, tmp_path, monkeypatch):
    monkeypatch.setenv("COVERS_DIR", str(tmp_path))
    game = await make_game(db)

    resp = await admin_client.put(
        f"/api/v1/admin/games/{game.id}/cover",
        json={"image_base64": "not-valid-base64!!!", "extension": "jpg"},
    )

    assert resp.status_code == 422


async def test_cover_upload_updates_existing_custom_cover(admin_client, db, admin_user, tmp_path, monkeypatch):
    """Re-uploading overwrites the file and audit-logs the previous URL as `before`."""
    monkeypatch.setenv("COVERS_DIR", str(tmp_path))
    game = await make_game(db, "Cover Game")

    first = await admin_client.put(
        f"/api/v1/admin/games/{game.id}/cover",
        json={"image_base64": TINY_IMAGE_B64, "extension": "jpg"},
    )
    assert first.status_code == 200

    new_b64 = base64.b64encode(b"different_bytes").decode()
    second = await admin_client.put(
        f"/api/v1/admin/games/{game.id}/cover",
        json={"image_base64": new_b64, "extension": "png"},
    )

    assert second.status_code == 200
    body = second.json()
    assert body["cover_image_url"] == f"/covers/{game.id}.png"

    written = tmp_path / f"{game.id}.png"
    assert written.read_bytes() == base64.b64decode(new_b64)
