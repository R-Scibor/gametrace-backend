from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.models.session import GameSession, SessionSource, SessionStatus
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


async def test_fix_error_session_clears_restart_note(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        status=SessionStatus.ERROR,
        notes="Self-healing: bot restarted while session was ONGOING",
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=1).isoformat()},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == SessionStatus.COMPLETED
    assert resp.json()["notes"] is None
    later = await authed_client.get(f"/api/v1/sessions/{session.id}")
    assert later.status_code == 200
    assert later.json()["notes"] is None


async def test_patch_that_stays_error_keeps_the_note(authed_client, db, user):
    game = await make_game(db)
    note = "Self-healing: bot restarted while session was ONGOING"
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        status=SessionStatus.ERROR,
        notes=note,
    )

    resp = await authed_client.patch(f"/api/v1/sessions/{session.id}", json={})

    assert resp.status_code == 200
    assert resp.json()["status"] == SessionStatus.ERROR
    assert resp.json()["notes"] == note


async def test_patch_completed_session_keeps_existing_note(authed_client, db, user):
    game = await make_game(db)
    note = "kept"
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        notes=note,
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=0.5).isoformat()},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == SessionStatus.COMPLETED
    assert resp.json()["notes"] == note


async def test_patch_cannot_set_notes(authed_client, db, user):
    game = await make_game(db)
    note = "system owned"
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        status=SessionStatus.ERROR,
        notes=note,
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=1).isoformat(), "notes": "user text"},
    )

    assert resp.status_code == 422
    db.expire(session)
    await db.refresh(session)
    assert session.notes == note
    assert session.status == SessionStatus.ERROR


async def test_fix_trashed_error_clears_note_on_the_trash_list(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        status=SessionStatus.ERROR,
        notes="Self-healing: bot restarted while session was ONGOING",
        deleted_at=datetime.now(UTC),
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=1).isoformat()},
    )

    assert resp.status_code == 200
    assert resp.json()["notes"] is None
    trash = await authed_client.get("/api/v1/sessions/trash")
    assert trash.status_code == 200
    row = next(item for item in trash.json() if item["id"] == session.id)
    assert row["notes"] is None


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
    assert resp.json()["detail"] == "end_time must be after start_time"


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


async def test_patch_bot_session_flips_source_to_manual(authed_client, db, user):
    """Editing end_time on a BOT session marks times as user-attested."""
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        source=SessionSource.BOT,
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=0.5).isoformat()},
    )

    assert resp.status_code == 200
    assert resp.json()["source"] == SessionSource.MANUAL


async def test_patch_error_bot_session_flips_source_to_manual(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        status=SessionStatus.ERROR,
        source=SessionSource.BOT,
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=1).isoformat()},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == SessionStatus.COMPLETED
    assert data["source"] == SessionSource.MANUAL


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
        json={"end_time": dt(hours_ago=0).isoformat()},
    )

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
        json={"end_time": dt(hours_ago=0).isoformat()},
    )

    assert resp.status_code == 404


async def test_patch_trashed_session_allows_edit(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        deleted_at=datetime.now(UTC),
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=0.5).isoformat()},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["duration_seconds"] == 9000  # 2.5h


async def test_patch_trashed_session_skips_overlap_check(authed_client, db, user):
    """Trashed row can be edited into times that overlap a live session;
    the conflict is re-evaluated on restore, not on edit."""
    game = await make_game(db)
    # Live COMPLETED at [5h ago, 2h ago]
    await make_session(db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=2))
    # Trashed at [4h ago, 3.5h ago]
    trashed = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=4), dt(hours_ago=3.5),
        deleted_at=datetime.now(UTC),
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{trashed.id}",
        json={"end_time": dt(hours_ago=2.5).isoformat()},  # overlaps the live row
    )

    assert resp.status_code == 200  # no overlap check while trashed


async def test_patch_flicker_session_returns_404(authed_client, db, user):
    """PATCH /sessions/{id} on a flicker row returns 404."""
    game = await make_game(db)
    flicker = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=3), dt(hours_ago=1),
        is_flicker=True,
    )

    resp = await authed_client.patch(
        f"/api/v1/sessions/{flicker.id}",
        json={"end_time": dt(hours_ago=0.5).isoformat()},
    )

    assert resp.status_code == 404


async def test_patch_with_discard_field_rejected_as_unknown(authed_client, db, user):
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

    assert resp.status_code == 422


async def test_patch_duration_rejected(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=49), dt(hours_ago=48),
    )
    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=0).isoformat()},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "session duration exceeds 48 hours"


async def test_patch_future_end_rejected(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=1),
    )
    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_from_now=10 / 60).isoformat()},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "end_time cannot be in the future"


async def test_patch_error_older_than_30_days_allowed_if_duration_ok(authed_client, db, user):
    game = await make_game(db)
    start = dt(hours_ago=31 * 24)
    session = await make_session(
        db, user.discord_id, game.id, start, start + timedelta(hours=1),
        status=SessionStatus.ERROR,
    )
    new_end = start + timedelta(hours=2)
    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": new_end.isoformat()},
    )
    assert resp.status_code == 200
    assert resp.json()["duration_seconds"] == 7200
    assert resp.json()["source"] == SessionSource.MANUAL


async def test_patch_completed_older_than_30_days_allowed_if_duration_ok(authed_client, db, user):
    game = await make_game(db)
    start = dt(hours_ago=31 * 24)
    session = await make_session(
        db, user.discord_id, game.id, start, start + timedelta(hours=1),
    )
    new_end = start + timedelta(hours=2)
    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": new_end.isoformat()},
    )
    assert resp.status_code == 200
    assert resp.json()["duration_seconds"] == 7200


async def test_patch_error_fix_to_now_over_48h_rejected(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=49), dt(hours_ago=48),
        status=SessionStatus.ERROR,
    )
    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=0).isoformat()},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "session duration exceeds 48 hours"


async def test_patch_over_duration_that_also_overlaps_is_422_not_409(authed_client, db, user):
    game = await make_game(db)
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=2), dt(hours_ago=1),
    )
    session = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=49), dt(hours_ago=48),
        status=SessionStatus.ERROR,
    )
    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=0).isoformat()},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "session duration exceeds 48 hours"


async def test_patch_naive_iso_end_time_never_500(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=1),
    )
    naive_end = dt(hours_ago=0.5).replace(tzinfo=None)
    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": naive_end.isoformat()},
    )
    assert resp.status_code == 200
    assert resp.json()["duration_seconds"] == 9000  # 2.5h


async def test_patch_trashed_over_duration_rejected(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=49), dt(hours_ago=48),
        deleted_at=datetime.now(UTC),
    )
    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=0).isoformat()},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "session duration exceeds 48 hours"


async def test_patch_ongoing_over_duration_returns_403_not_422(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=50),
        status=SessionStatus.ONGOING, source=SessionSource.BOT,
    )
    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=0).isoformat()},
    )
    assert resp.status_code == 403


async def test_patch_trashed_future_end_rejected(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=1),
        deleted_at=datetime.now(UTC),
    )
    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_from_now=10 / 60).isoformat()},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "end_time cannot be in the future"


async def test_patch_returns_409_when_another_writer_changed_status(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(
        db,
        user.discord_id,
        game.id,
        dt(hours_ago=3),
        dt(hours_ago=1),
        status=SessionStatus.COMPLETED,
    )
    await db.execute(
        update(GameSession)
        .where(GameSession.id == session.id)
        .values(status=SessionStatus.ONGOING, end_time=None, duration_seconds=None),
        execution_options={"synchronize_session": False},
    )
    await db.commit()

    resp = await authed_client.patch(
        f"/api/v1/sessions/{session.id}",
        json={"end_time": dt(hours_ago=0.5).isoformat()},
    )

    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["detail"] == "Session was updated by another writer"
    assert body["detail"]["conflicting_session"]["status"] == "ONGOING"
    db.expire(session)
    await db.refresh(session)
    assert session.status == SessionStatus.ONGOING
    assert session.end_time is None
