"""Account purge sweeper — unit tests.

Async tests call _run_purge(db) directly so the rollback fixture keeps the
test DB clean. The sync Celery entry (.run()) isn't exercised — it just wraps
_run_purge_with_engine in asyncio.run.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.celery_app import celery_app
from app.models.game import UserGamePreference
from app.models.report import Report
from app.models.session import GameSession
from app.models.user import User, UserAuthToken, UserDevice
from app.models.voice_usage import VoiceUsage
from app.tasks.cleanup import _run_purge
from tests.factories import (
    make_device,
    make_game,
    make_pref,
    make_report,
    make_session,
    make_token,
    make_user,
)


async def test_purges_user_past_grace_period(db):
    now = datetime.now(timezone.utc)
    scheduled = await make_user(
        db,
        discord_id="800000000000000001",
        username="scheduled_past",
        deletion_requested_at=now - timedelta(days=8),
        purge_at=now - timedelta(hours=1),
    )

    deleted = await _run_purge(db)

    assert deleted == 1
    remaining = (
        await db.execute(select(User).where(User.discord_id == scheduled.discord_id))
    ).scalar_one_or_none()
    assert remaining is None


async def test_leaves_user_with_future_purge_at(db):
    now = datetime.now(timezone.utc)
    scheduled = await make_user(
        db,
        discord_id="800000000000000002",
        username="scheduled_future",
        deletion_requested_at=now,
        purge_at=now + timedelta(days=6),
    )

    deleted = await _run_purge(db)

    assert deleted == 0
    remaining = (
        await db.execute(select(User).where(User.discord_id == scheduled.discord_id))
    ).scalar_one_or_none()
    assert remaining is not None


async def test_leaves_unscheduled_user(db, user):
    deleted = await _run_purge(db)

    assert deleted == 0
    remaining = (
        await db.execute(select(User).where(User.discord_id == user.discord_id))
    ).scalar_one_or_none()
    assert remaining is not None


async def test_cascade_removes_all_owned_data(db):
    now = datetime.now(timezone.utc)
    scheduled = await make_user(
        db,
        discord_id="800000000000000003",
        username="scheduled_cascade",
        deletion_requested_at=now - timedelta(days=8),
        purge_at=now - timedelta(hours=1),
    )
    game = await make_game(db, primary_name="CascadeGame")
    session = await make_session(
        db, scheduled.discord_id, game.id,
        now - timedelta(days=5), now - timedelta(days=4),
    )
    pref = await make_pref(db, scheduled.discord_id, game.id, is_ignored=True)
    await make_token(db, scheduled.discord_id)
    device = await make_device(db, scheduled.discord_id, "cascade-tok")
    report = await make_report(db, scheduled.discord_id, message="cascade bug")
    voice = VoiceUsage(
        user_id=scheduled.discord_id,
        game_resolved=True,
        fields_extracted=3,
    )
    db.add(voice)
    await db.flush()
    voice_id = voice.id

    deleted = await _run_purge(db)

    assert deleted == 1

    assert (await db.execute(
        select(GameSession).where(GameSession.id == session.id)
    )).scalar_one_or_none() is None
    assert (await db.execute(
        select(UserGamePreference).where(UserGamePreference.id == pref.id)
    )).scalar_one_or_none() is None
    assert (await db.execute(
        select(UserAuthToken).where(UserAuthToken.user_id == scheduled.discord_id)
    )).scalar_one_or_none() is None
    assert (await db.execute(
        select(UserDevice).where(UserDevice.id == device.id)
    )).scalar_one_or_none() is None
    assert (await db.execute(
        select(Report).where(Report.id == report.id)
    )).scalar_one_or_none() is None
    assert (await db.execute(
        select(VoiceUsage).where(VoiceUsage.id == voice_id)
    )).scalar_one_or_none() is None

    # Catalog game itself is untouched.
    assert (await db.execute(
        select(type(game)).where(type(game).id == game.id)
    )).scalar_one_or_none() is not None


async def test_other_users_and_catalog_untouched(db, user):
    now = datetime.now(timezone.utc)
    scheduled = await make_user(
        db,
        discord_id="800000000000000004",
        username="scheduled_isolated",
        deletion_requested_at=now - timedelta(days=8),
        purge_at=now - timedelta(hours=1),
    )
    game = await make_game(db, primary_name="SharedGame")
    other_session = await make_session(
        db, user.discord_id, game.id,
        now - timedelta(days=3), now - timedelta(days=2),
    )
    scheduled_session = await make_session(
        db, scheduled.discord_id, game.id,
        now - timedelta(days=5), now - timedelta(days=4),
    )

    deleted = await _run_purge(db)

    assert deleted == 1
    assert (await db.execute(
        select(GameSession).where(GameSession.id == other_session.id)
    )).scalar_one_or_none() is not None
    assert (await db.execute(
        select(GameSession).where(GameSession.id == scheduled_session.id)
    )).scalar_one_or_none() is None
    assert (await db.execute(
        select(User).where(User.discord_id == user.discord_id)
    )).scalar_one_or_none() is not None
    assert (await db.execute(
        select(type(game)).where(type(game).id == game.id)
    )).scalar_one_or_none() is not None


async def test_running_twice_is_harmless(db):
    now = datetime.now(timezone.utc)
    await make_user(
        db,
        discord_id="800000000000000005",
        username="scheduled_idempotent",
        deletion_requested_at=now - timedelta(days=8),
        purge_at=now - timedelta(hours=1),
    )

    first = await _run_purge(db)
    second = await _run_purge(db)

    assert first == 1
    assert second == 0


def test_beat_schedule_has_purge_deleted_accounts():
    sched = celery_app.conf.beat_schedule
    assert "purge_deleted_accounts" in sched
    assert sched["purge_deleted_accounts"]["task"] == "tasks.purge_deleted_accounts"
