from datetime import datetime, timezone

from app.models.session import SessionSource, SessionStatus
from tests.factories import dt, make_game, make_session, make_user


async def test_restore_completed_session(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.post(f"/api/v1/sessions/{session.id}/restore")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == SessionStatus.COMPLETED
    assert data["deleted_at"] is None


async def test_restore_error_session_preserves_status(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        status=SessionStatus.ERROR,
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.post(f"/api/v1/sessions/{session.id}/restore")
    assert resp.status_code == 200
    assert resp.json()["status"] == SessionStatus.ERROR


async def test_restore_completed_with_overlap_returns_409(authed_client, db, user):
    game = await make_game(db)
    # Live COMPLETED at [5h ago, 2h ago]
    live = await make_session(db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=2))
    # Trashed COMPLETED at [4h ago, 3h ago] — overlaps live when restored
    trashed = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=4), dt(hours_ago=3),
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.post(f"/api/v1/sessions/{trashed.id}/restore")
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["conflicting_session"]["id"] == live.id


async def test_restore_error_session_with_overlapping_live_succeeds(authed_client, db, user):
    """ERROR has no end_time, so no overlap is possible."""
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=2))
    trashed = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=4),
        status=SessionStatus.ERROR,
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.post(f"/api/v1/sessions/{trashed.id}/restore")
    assert resp.status_code == 200


async def test_restore_non_trashed_returns_404(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
    )

    resp = await authed_client.post(f"/api/v1/sessions/{session.id}/restore")
    assert resp.status_code == 404


async def test_restore_other_users_session_returns_404(authed_client, db):
    game = await make_game(db)
    other = await make_user(db, discord_id="444444444444444444", username="other3")
    session = await make_session(
        db, other.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.post(f"/api/v1/sessions/{session.id}/restore")
    assert resp.status_code == 404


async def test_edit_then_restore_resolves_overlap(authed_client, db, user):
    """Realistic flow: PATCH trashed row to non-overlapping times, then restore.

    Live session: [5h ago, 4.5h ago].
    Trashed original: [4h ago, 3h ago] — no overlap with live.
    After PATCH end_time=3.5h ago, still no overlap → restore succeeds.
    The key property being tested: PATCH on a trashed row is allowed (no overlap
    check against live sessions) and the subsequent restore succeeds when
    end_time is genuinely non-conflicting.
    """
    game = await make_game(db)
    # Live session in a narrow window that does NOT overlap with trashed
    await make_session(db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4.5))
    trashed = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=4), dt(hours_ago=3),
        deleted_at=datetime.now(timezone.utc),
    )

    # PATCH while trashed — moves end_time (no overlap check on trashed rows)
    patch_resp = await authed_client.patch(
        f"/api/v1/sessions/{trashed.id}",
        json={"end_time": dt(hours_ago=3.5).isoformat()},
    )
    assert patch_resp.status_code == 200

    # Restore: overlap re-check runs; [-4h, -3.5h] does not conflict with [-5h, -4.5h]
    restore_resp = await authed_client.post(f"/api/v1/sessions/{trashed.id}/restore")
    assert restore_resp.status_code == 200
