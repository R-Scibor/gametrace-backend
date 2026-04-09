from datetime import datetime, timezone

from app.models.session import SessionSource, SessionStatus
from tests.factories import dt, make_game, make_session, make_user


# ── ERROR → COMPLETED (Fix) ───────────────────────────────────────────────────

async def test_fix_error_session(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=1).isoformat()},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == SessionStatus.COMPLETED
    assert data["duration_seconds"] == 7200


async def test_fix_end_time_before_start_returns_422(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=4).isoformat()},  # before start_time
    )

    assert resp.status_code == 422


async def test_fix_would_overlap_returns_409(authed_client, db, user):
    game = await make_game(db)
    # Existing COMPLETED session at [5h ago, 2h ago]
    await make_session(db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=2))
    # ERROR session at [4h ago, ...]
    error_session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=4), dt(hours_ago=3.5),
        status=SessionStatus.ERROR,
    )

    # Fixing with end_time that overlaps the COMPLETED session
    resp = await authed_client.patch(
        f"/api/v1/sessions/{error_session.id}",
        json={"end_time": dt(hours_ago=2.5).isoformat()},
    )

    assert resp.status_code == 409
    assert "conflicting_session" in resp.json()["detail"]


# ── COMPLETED → COMPLETED (Edit) ─────────────────────────────────────────────

async def test_edit_end_time_recalculates_duration(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=0.5).isoformat()},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == SessionStatus.COMPLETED
    # 2.5 hours from start (3h ago) to new end (0.5h ago)
    assert data["duration_seconds"] == 9000


async def test_edit_notes_only(authed_client, db, user):
    game = await make_game(db)
    original_end = dt(hours_ago=1)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=2), original_end,
        notes="old note",
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"notes": "updated note"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["notes"] == "updated note"
    # end_time should not change
    assert data["duration_seconds"] == session.duration_seconds


async def test_edit_notes_to_null(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=2), dt(hours_ago=1),
        notes="some note",
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"notes": None},
    )

    assert resp.status_code == 200
    assert resp.json()["notes"] is None


# ── ONGOING (bot-managed) ─────────────────────────────────────────────────────

async def test_cannot_edit_ongoing_session(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"notes": "trying to edit"},
    )

    assert resp.status_code == 403


# ── Discard (ERROR → soft-delete) ────────────────────────────────────────────

async def test_discard_error_session(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"discard": True},
    )

    assert resp.status_code == 200

    # Subsequent GET should return 404
    get_resp = await authed_client.get(f"/api/v1/sessions/{session.id}")
    assert get_resp.status_code == 404


async def test_cannot_discard_completed_session(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=2), dt(hours_ago=1),
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"discard": True},
    )

    assert resp.status_code == 422


async def test_cannot_discard_ongoing_session(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"discard": True},
    )

    # ONGOING guard fires before discard check
    assert resp.status_code == 403


# ── Auth / ownership ──────────────────────────────────────────────────────────

async def test_cannot_patch_other_users_session(authed_client, db):
    game = await make_game(db)
    other = await make_user(db, discord_id="222222222222222222", username="otheruser")
    session = await make_session(
        db, other.discord_id, game.id,
        dt(hours_ago=2), dt(hours_ago=1),
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"notes": "hacking"},
    )

    assert resp.status_code == 404


async def test_patch_soft_deleted_returns_404(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=2), dt(hours_ago=1),
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"notes": "should not work"},
    )

    assert resp.status_code == 404
