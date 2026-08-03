from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.user import User
from tests.factories import make_user


async def test_deletion_columns_round_trip(db):
    purge_at = datetime.now(timezone.utc) + timedelta(days=7)
    user = await make_user(
        db,
        discord_id="444444444444444444",
        username="deletion_user",
        deletion_requested_at=purge_at - timedelta(days=7),
        purge_at=purge_at,
    )

    fetched = await db.get(User, user.discord_id)
    assert fetched.deletion_requested_at == purge_at - timedelta(days=7)
    assert fetched.purge_at == purge_at


async def test_deletion_columns_default_none(db):
    user = await make_user(db, discord_id="555555555555555555", username="plain_user")

    fetched = (
        await db.execute(select(User).where(User.discord_id == user.discord_id))
    ).scalar_one()
    assert fetched.deletion_requested_at is None
    assert fetched.purge_at is None
