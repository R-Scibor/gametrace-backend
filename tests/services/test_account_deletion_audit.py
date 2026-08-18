"""Audit rows for schedule_deletion / cancel_deletion."""

import fakeredis.aioredis
import pytest
from sqlalchemy import func, select

from app.models.account_deletion_event import (
    EVENT_CANCELLED,
    EVENT_REQUESTED,
    AccountDeletionEvent,
)
from app.services.account_deletion import cancel_deletion, schedule_deletion
from tests.factories import make_user


@pytest.fixture
async def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def patch_get_redis(monkeypatch, redis_client):
    monkeypatch.setattr("app.services.account_deletion.get_redis", lambda: redis_client)


async def test_schedule_inserts_requested_event(db):
    user = await make_user(
        db, discord_id="900000000000000001", username="audit_sched"
    )

    user = await schedule_deletion(db, user)

    rows = (
        await db.execute(
            select(AccountDeletionEvent).where(
                AccountDeletionEvent.discord_id == user.discord_id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].event == EVENT_REQUESTED
    assert rows[0].purge_at == user.purge_at
    assert rows[0].purge_at is not None


async def test_schedule_idempotent_does_not_double_insert(db):
    user = await make_user(
        db, discord_id="900000000000000002", username="audit_idem"
    )
    await schedule_deletion(db, user)
    await db.refresh(user)
    await schedule_deletion(db, user)

    count = (
        await db.execute(
            select(func.count())
            .select_from(AccountDeletionEvent)
            .where(AccountDeletionEvent.discord_id == "900000000000000002")
        )
    ).scalar_one()
    assert count == 1


async def test_cancel_inserts_cancelled_event(db):
    user = await make_user(
        db, discord_id="900000000000000003", username="audit_cancel"
    )
    user = await schedule_deletion(db, user)
    await db.refresh(user)

    ok = await cancel_deletion(db, user)
    assert ok is True

    events = (
        await db.execute(
            select(AccountDeletionEvent)
            .where(AccountDeletionEvent.discord_id == "900000000000000003")
            .order_by(AccountDeletionEvent.id)
        )
    ).scalars().all()
    assert [e.event for e in events] == [EVENT_REQUESTED, EVENT_CANCELLED]
    assert events[1].purge_at is None


async def test_cancel_when_unscheduled_inserts_nothing(db):
    user = await make_user(
        db, discord_id="900000000000000004", username="audit_noshced"
    )

    ok = await cancel_deletion(db, user)
    assert ok is False

    count = (
        await db.execute(
            select(func.count())
            .select_from(AccountDeletionEvent)
            .where(AccountDeletionEvent.discord_id == "900000000000000004")
        )
    ).scalar_one()
    assert count == 0
