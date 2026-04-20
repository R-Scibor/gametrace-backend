"""Hard delete sweeper — unit tests.

Async tests call _run_cleanup(db) directly so the rollback fixture keeps the
test DB clean. The sync Celery entry (.run()) isn't exercised — it just wraps
_run_with_engine in asyncio.run.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.celery_app import celery_app
from app.models.session import GameSession
from app.models.user import UserDevice
from app.tasks.cleanup import _run_cleanup
from tests.factories import make_device, make_game, make_session, make_user


async def test_removes_old_soft_deleted_sessions(db, user):
    game = await make_game(db, primary_name="OldGame")
    now = datetime.now(timezone.utc)

    old = await make_session(
        db, user.discord_id, game.id,
        now - timedelta(days=20), now - timedelta(days=19),
        deleted_at=now - timedelta(days=8),
    )
    recent = await make_session(
        db, user.discord_id, game.id,
        now - timedelta(days=5), now - timedelta(days=4),
        deleted_at=now - timedelta(days=2),
    )
    live = await make_session(
        db, user.discord_id, game.id,
        now - timedelta(days=3), now - timedelta(days=2),
    )

    sessions_deleted, _ = await _run_cleanup(db)

    assert sessions_deleted == 1
    remaining_ids = {
        row.id for row in
        (await db.execute(select(GameSession))).scalars().all()
    }
    assert old.id not in remaining_ids
    assert recent.id in remaining_ids
    assert live.id in remaining_ids


async def test_removes_stale_devices(db, user):
    fresh = await make_device(db, user.discord_id, "fresh-tok")
    stale = await make_device(db, user.discord_id, "stale-tok")
    stale.last_active = datetime.now(timezone.utc) - timedelta(days=200)
    await db.flush()

    _, devices_deleted = await _run_cleanup(db)

    assert devices_deleted == 1
    remaining = {
        row.fcm_token for row in
        (await db.execute(select(UserDevice))).scalars().all()
    }
    assert "fresh-tok" in remaining
    assert "stale-tok" not in remaining
    assert fresh.id is not None  # reference to silence "unused" lints


async def test_no_op_when_nothing_stale(db, user):
    game = await make_game(db)
    now = datetime.now(timezone.utc)
    await make_session(
        db, user.discord_id, game.id,
        now - timedelta(hours=3), now - timedelta(hours=2),
    )
    await make_device(db, user.discord_id, "live-tok")

    sessions_deleted, devices_deleted = await _run_cleanup(db)

    assert sessions_deleted == 0
    assert devices_deleted == 0


def test_beat_schedule_has_hard_delete_sweep():
    sched = celery_app.conf.beat_schedule
    assert "hard_delete_sweep" in sched
    assert sched["hard_delete_sweep"]["task"] == "tasks.hard_delete_sweep"
