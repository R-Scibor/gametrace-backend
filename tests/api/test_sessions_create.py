from datetime import datetime, timezone

import pytest

from app.models.session import SessionSource, SessionStatus
from tests.factories import dt, make_game, make_session


async def test_create_session_success(authed_client, db):
    game = await make_game(db)

    resp = await authed_client.post(
        "/api/v1/sessions",
        json={
            "game_id": game.id,
            "start_time": dt(hours_ago=2).isoformat(),
            "end_time": dt(hours_ago=1).isoformat(),
        },
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["game_id"] == game.id
    assert data["status"] == SessionStatus.COMPLETED
    assert data["source"] == SessionSource.MANUAL
    assert data["duration_seconds"] == 3600


async def test_create_session_game_not_found(authed_client):
    resp = await authed_client.post(
        "/api/v1/sessions",
        json={
            "game_id": 999999,
            "start_time": dt(hours_ago=2).isoformat(),
            "end_time": dt(hours_ago=1).isoformat(),
        },
    )

    assert resp.status_code == 404


async def test_create_session_end_before_start_rejected(authed_client, db):
    game = await make_game(db)

    resp = await authed_client.post(
        "/api/v1/sessions",
        json={
            "game_id": game.id,
            "start_time": dt(hours_ago=1).isoformat(),
            "end_time": dt(hours_ago=2).isoformat(),  # before start
        },
    )

    assert resp.status_code == 422


async def test_create_session_overlap_with_completed(authed_client, db, user):
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=1))

    resp = await authed_client.post(
        "/api/v1/sessions",
        json={
            "game_id": game.id,
            "start_time": dt(hours_ago=2).isoformat(),  # overlaps [3h ago, 1h ago]
            "end_time": dt(hours_ago=0.5).isoformat(),
        },
    )

    assert resp.status_code == 409


async def test_create_session_overlap_with_ongoing(authed_client, db, user):
    game = await make_game(db)
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=2),
        status=SessionStatus.ONGOING, source=SessionSource.BOT
    )

    resp = await authed_client.post(
        "/api/v1/sessions",
        json={
            "game_id": game.id,
            "start_time": dt(hours_ago=1).isoformat(),
            "end_time": dt(hours_from_now=1).isoformat(),
        },
    )

    assert resp.status_code == 409


async def test_create_session_overlap_with_error_session(authed_client, db, user):
    game = await make_game(db)
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=1),
        status=SessionStatus.ERROR
    )

    resp = await authed_client.post(
        "/api/v1/sessions",
        json={
            "game_id": game.id,
            "start_time": dt(hours_ago=2).isoformat(),
            "end_time": dt(hours_ago=0.5).isoformat(),
        },
    )

    assert resp.status_code == 409


async def test_create_session_adjacent_does_not_conflict(authed_client, db, user):
    """Sessions that touch at a single point (end == start) are not overlapping."""
    game = await make_game(db)
    boundary = dt(hours_ago=1)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=2), boundary)

    resp = await authed_client.post(
        "/api/v1/sessions",
        json={
            "game_id": game.id,
            "start_time": boundary.isoformat(),  # starts exactly when previous ends
            "end_time": dt(hours_from_now=1).isoformat(),
        },
    )

    assert resp.status_code == 201


async def test_create_session_soft_deleted_not_counted_as_overlap(authed_client, db, user):
    game = await make_game(db)
    deleted_at = datetime.now(timezone.utc)
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=1),
        deleted_at=deleted_at
    )

    resp = await authed_client.post(
        "/api/v1/sessions",
        json={
            "game_id": game.id,
            "start_time": dt(hours_ago=2).isoformat(),
            "end_time": dt(hours_ago=0.5).isoformat(),
        },
    )

    assert resp.status_code == 201


async def test_create_session_other_user_overlap_not_blocked(authed_client, db):
    """Overlapping sessions for DIFFERENT users are allowed."""
    game = await make_game(db)
    # Create a different user's session in the same time window
    from tests.factories import make_user
    other = await make_user(db, discord_id="222222222222222222", username="otheruser")
    await make_session(db, other.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=1))

    resp = await authed_client.post(
        "/api/v1/sessions",
        json={
            "game_id": game.id,
            "start_time": dt(hours_ago=2).isoformat(),
            "end_time": dt(hours_ago=0.5).isoformat(),
        },
    )

    assert resp.status_code == 201
