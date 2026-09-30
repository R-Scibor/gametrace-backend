from datetime import UTC, datetime

from sqlalchemy import select, update

from app.models.session import GameSession, SessionSource, SessionStatus
from tests.factories import dt, make_game, make_session, make_user


async def test_delete_completed_session(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
    )

    resp = await authed_client.delete(f"/api/v1/sessions/{session.id}")
    assert resp.status_code == 204

    get_resp = await authed_client.get(f"/api/v1/sessions/{session.id}")
    assert get_resp.status_code == 404


async def test_delete_error_session(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.delete(f"/api/v1/sessions/{session.id}")
    assert resp.status_code == 204


async def test_cannot_delete_ongoing_session(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    resp = await authed_client.delete(f"/api/v1/sessions/{session.id}")
    assert resp.status_code == 403


async def test_delete_already_trashed_returns_404(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        deleted_at=datetime.now(UTC),
    )

    resp = await authed_client.delete(f"/api/v1/sessions/{session.id}")
    assert resp.status_code == 404


async def test_delete_other_users_session_returns_404(authed_client, db):
    game = await make_game(db)
    other = await make_user(db, discord_id="222222222222222222", username="otheruser")
    session = await make_session(
        db, other.discord_id, game.id,
        dt(hours_ago=2), dt(hours_ago=1),
    )

    resp = await authed_client.delete(f"/api/v1/sessions/{session.id}")
    assert resp.status_code == 404


async def test_session_response_includes_deleted_at_field(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
    )

    resp = await authed_client.get(f"/api/v1/sessions/{session.id}")
    assert resp.status_code == 200
    assert "deleted_at" in resp.json()
    assert resp.json()["deleted_at"] is None


async def test_hard_delete_trashed_session(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        deleted_at=datetime.now(UTC),
    )
    session_id = session.id

    resp = await authed_client.delete(f"/api/v1/sessions/{session_id}?hard=true")
    assert resp.status_code == 204

    result = await db.execute(select(GameSession).where(GameSession.id == session_id))
    assert result.scalar_one_or_none() is None


async def test_hard_delete_live_session_returns_422(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
    )

    resp = await authed_client.delete(f"/api/v1/sessions/{session.id}?hard=true")
    assert resp.status_code == 422


async def test_delete_flicker_session_returns_404(authed_client, db, user):
    """DELETE /sessions/{id} on a flicker row returns 404."""
    game = await make_game(db)
    flicker = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        is_flicker=True,
    )

    resp = await authed_client.delete(f"/api/v1/sessions/{flicker.id}")

    assert resp.status_code == 404


async def test_hard_delete_other_users_session_returns_404(authed_client, db):
    game = await make_game(db)
    other = await make_user(db, discord_id="333333333333333333", username="other2")
    session = await make_session(
        db, other.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        deleted_at=datetime.now(UTC),
    )

    resp = await authed_client.delete(f"/api/v1/sessions/{session.id}?hard=true")
    assert resp.status_code == 404


async def test_delete_returns_409_when_another_writer_changed_status(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db,
        user.discord_id,
        game.id,
        dt(hours_ago=3),
        dt(hours_ago=1),
    )
    await db.execute(
        update(GameSession)
        .where(GameSession.id == session.id)
        .values(status=SessionStatus.ONGOING, end_time=None, duration_seconds=None),
        execution_options={"synchronize_session": False},
    )
    await db.commit()

    resp = await authed_client.delete(f"/api/v1/sessions/{session.id}")

    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["detail"] == "Session was updated by another writer"
    assert body["detail"]["conflicting_session"]["status"] == "ONGOING"
    db.expire(session)
    await db.refresh(session)
    assert session.status == SessionStatus.ONGOING
    assert session.deleted_at is None


async def test_delete_returns_404_when_row_was_trashed_underneath(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db,
        user.discord_id,
        game.id,
        dt(hours_ago=3),
        dt(hours_ago=1),
    )
    await db.execute(
        update(GameSession)
        .where(GameSession.id == session.id)
        .values(deleted_at=datetime.now(UTC)),
        execution_options={"synchronize_session": False},
    )
    await db.commit()

    resp = await authed_client.delete(f"/api/v1/sessions/{session.id}")

    assert resp.status_code == 404
    db.expire(session)
    await db.refresh(session)
    assert session.deleted_at is not None
    assert session.status == SessionStatus.COMPLETED
