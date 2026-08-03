from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.models.session import SessionSource, SessionStatus
from app.models.user import UserAuthToken, UserDevice
from tests.factories import make_alias, make_device, make_game, make_session, make_token, make_user


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


async def test_schedule_deletion_returns_202_with_grace_period(authed_client, db, user):
    resp = await authed_client.post("/api/v1/profile/me/deletion")

    assert resp.status_code == 202
    body = resp.json()

    requested_at = datetime.fromisoformat(body["deletion_requested_at"])
    purge_at = datetime.fromisoformat(body["purge_at"])
    assert purge_at == requested_at + timedelta(days=settings.account_deletion_grace_days)

    await db.refresh(user)
    assert user.deletion_requested_at == requested_at
    assert user.purge_at == purge_at


async def test_schedule_deletion_revokes_all_auth_tokens(authed_client, db, user):
    # A second token for the same user, so we can prove ALL of them get wiped,
    # not just the one used to make this call.
    await make_token(db, user.discord_id)

    resp = await authed_client.post("/api/v1/profile/me/deletion")
    assert resp.status_code == 202

    result = await db.execute(
        select(UserAuthToken).where(UserAuthToken.user_id == user.discord_id)
    )
    assert result.scalars().all() == []


async def test_schedule_deletion_removes_all_devices(authed_client, db, user):
    await make_device(db, user.discord_id, fcm_token="fcm-token-1")
    await make_device(db, user.discord_id, fcm_token="fcm-token-2")

    resp = await authed_client.post("/api/v1/profile/me/deletion")
    assert resp.status_code == 202

    result = await db.execute(select(UserDevice).where(UserDevice.user_id == user.discord_id))
    assert result.scalars().all() == []


async def test_schedule_deletion_errors_ongoing_session(authed_client, db, user):
    game = await make_game(db)
    await make_alias(db, game.id, "test.exe")
    session = await make_session(
        db,
        user_id=user.discord_id,
        game_id=game.id,
        start_time=datetime.now(timezone.utc) - timedelta(hours=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    resp = await authed_client.post("/api/v1/profile/me/deletion")
    assert resp.status_code == 202

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert session.notes


async def test_schedule_deletion_leaves_completed_session_untouched(authed_client, db, user):
    game = await make_game(db)
    await make_alias(db, game.id, "test2.exe")
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    end = start + timedelta(hours=1)
    session = await make_session(
        db,
        user_id=user.discord_id,
        game_id=game.id,
        start_time=start,
        end_time=end,
        status=SessionStatus.COMPLETED,
    )

    resp = await authed_client.post("/api/v1/profile/me/deletion")
    assert resp.status_code == 202

    await db.refresh(session)
    assert session.status == SessionStatus.COMPLETED
    assert session.notes is None


async def test_schedule_deletion_admin_gets_403(admin_client):
    resp = await admin_client.post("/api/v1/profile/me/deletion")
    assert resp.status_code == 403


async def test_schedule_deletion_idempotent_with_fresh_token(authed_client, db, user):
    first = await authed_client.post("/api/v1/profile/me/deletion")
    assert first.status_code == 202
    first_purge_at = first.json()["purge_at"]

    fresh_token = await make_token(db, user.discord_id)
    authed_client.headers.update({"Authorization": f"Bearer {fresh_token}"})

    second = await authed_client.post("/api/v1/profile/me/deletion")
    assert second.status_code == 202
    assert second.json()["purge_at"] == first_purge_at


async def test_schedule_deletion_original_bearer_401_after_call(client, db, user):
    token = await make_token(db, user.discord_id)
    client.headers.update({"Authorization": f"Bearer {token}"})

    resp = await client.post("/api/v1/profile/me/deletion")
    assert resp.status_code == 202

    again = await client.get("/api/v1/profile/me")
    assert again.status_code == 401
