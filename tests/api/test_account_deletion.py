from datetime import datetime, timedelta, timezone

from tests.factories import make_user


async def test_deletion_columns_round_trip(db):
    purge_at = datetime.now(timezone.utc) + timedelta(days=7)
    requested_at = purge_at - timedelta(days=7)
    user = await make_user(
        db,
        discord_id="444444444444444444",
        username="deletion_user",
        deletion_requested_at=requested_at,
        purge_at=purge_at,
    )

    # Force the in-memory attributes to be discarded and re-read from Postgres,
    # so this actually proves the values were persisted rather than just still
    # sitting on the Python object make_user() constructed.
    await db.refresh(user)

    assert user.deletion_requested_at == requested_at
    assert user.purge_at == purge_at


async def test_deletion_columns_default_none(db):
    user = await make_user(db, discord_id="555555555555555555", username="plain_user")

    await db.refresh(user)

    assert user.deletion_requested_at is None
    assert user.purge_at is None
