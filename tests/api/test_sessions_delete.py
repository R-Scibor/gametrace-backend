from datetime import datetime, timezone

from app.models.session import SessionSource, SessionStatus
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
        deleted_at=datetime.now(timezone.utc),
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
