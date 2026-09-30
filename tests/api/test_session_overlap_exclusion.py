"""A write that races past the overlap SELECT still returns the existing 409."""

from datetime import UTC, datetime

from app.api.v1.endpoints import sessions as sessions_endpoint
from app.models.session import SessionStatus
from tests.factories import dt, make_game, make_session


async def _miss_once(monkeypatch):
    real = sessions_endpoint._check_overlap
    calls = {"n": 0}

    async def _skip_first(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real(*args, **kwargs)

    monkeypatch.setattr(sessions_endpoint, "_check_overlap", _skip_first)


async def test_create_maps_exclusion_violation_to_409(authed_client, db, user, monkeypatch):
    game = await make_game(db)
    existing = await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=1))
    await db.commit()
    existing_id = existing.id
    await _miss_once(monkeypatch)

    resp = await authed_client.post(
        "/api/v1/sessions",
        json={
            "game_id": game.id,
            "start_time": dt(hours_ago=2).isoformat(),
            "end_time": dt(hours_ago=0.5).isoformat(),
        },
    )

    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["detail"] == "Session overlaps with an existing session"
    assert body["detail"]["conflicting_session"]["id"] == existing_id


async def test_patch_maps_exclusion_violation_to_409(authed_client, db, user, monkeypatch):
    game = await make_game(db)
    existing = await make_session(db, user.discord_id, game.id, dt(hours_ago=2), dt(hours_ago=1))
    editable = await make_session(db, user.discord_id, game.id, dt(hours_ago=4), dt(hours_ago=3))
    await db.commit()
    existing_id = existing.id
    await _miss_once(monkeypatch)

    resp = await authed_client.patch(
        f"/api/v1/sessions/{editable.id}",
        json={"end_time": dt(hours_ago=1.5).isoformat()},
    )

    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["detail"] == "Session overlaps with an existing session"
    assert body["detail"]["conflicting_session"]["id"] == existing_id
    await db.refresh(editable)
    assert editable.end_time is not None
    assert editable.status == SessionStatus.COMPLETED


async def test_restore_maps_exclusion_violation_to_409(authed_client, db, user, monkeypatch):
    game = await make_game(db)
    existing = await make_session(db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=2))
    trashed = await make_session(
        db,
        user.discord_id,
        game.id,
        dt(hours_ago=4),
        dt(hours_ago=3),
        deleted_at=datetime.now(UTC),
    )
    await db.commit()
    existing_id = existing.id
    trashed_id = trashed.id
    await _miss_once(monkeypatch)

    resp = await authed_client.post(f"/api/v1/sessions/{trashed_id}/restore")

    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["detail"] == "Session overlaps with an existing session"
    assert body["detail"]["conflicting_session"]["id"] == existing_id
    await db.refresh(trashed)
    assert trashed.deleted_at is not None
