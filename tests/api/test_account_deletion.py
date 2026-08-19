from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.session import SessionSource, SessionStatus
from app.models.user import UserAuthToken, UserDevice
from tests.factories import make_alias, make_device, make_game, make_session, make_token, make_user


@pytest.fixture
async def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def patch_get_redis(monkeypatch, redis_client):
    # schedule_deletion() calls the module-level get_redis() in
    # app.services.account_deletion; without this patch the real cached client
    # in app.core.redis is used, which breaks across pytest's per-test event
    # loops (see tests/api/test_auth_link.py for the same pattern).
    monkeypatch.setattr("app.services.account_deletion.get_redis", lambda: redis_client)


async def test_deletion_columns_round_trip(db):
    purge_at = datetime.now(UTC) + timedelta(days=7)
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


async def test_schedule_deletion_202_reports_the_grace_days_it_applied(
    authed_client, db, user, monkeypatch
):
    monkeypatch.setattr(settings, "account_deletion_grace_days", 3)

    resp = await authed_client.post("/api/v1/profile/me/deletion")

    assert resp.status_code == 202
    body = resp.json()
    assert body["grace_days"] == 3
    requested_at = datetime.fromisoformat(body["deletion_requested_at"])
    purge_at = datetime.fromisoformat(body["purge_at"])
    assert purge_at - requested_at == timedelta(days=3)


async def test_grace_days_comes_from_the_row_not_the_current_setting(db, client, monkeypatch):
    """An account keeps the window it was scheduled under, even if the env changes."""
    user = await make_user(
        db,
        discord_id="777777777777777777",
        username="long-grace",
        deletion_requested_at=datetime.now(UTC) - timedelta(days=30),
        purge_at=datetime.now(UTC) + timedelta(days=5),
    )
    token = await make_token(db, user.discord_id)
    client.headers.update({"Authorization": f"Bearer {token}"})
    monkeypatch.setattr(settings, "account_deletion_grace_days", 7)

    resp = await client.get("/api/v1/profile/me")

    assert resp.status_code == 403
    # 35 days between the two stamps — not the 7 currently configured.
    assert resp.json()["detail"]["grace_days"] == 35


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
        start_time=datetime.now(UTC) - timedelta(hours=1),
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
    start = datetime.now(UTC) - timedelta(hours=2)
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


async def test_cancel_deletion_clears_columns_and_returns_200(client, db):
    user = await make_user(
        db,
        discord_id="666666666666666666",
        username="cancel_user",
        deletion_requested_at=datetime.now(UTC) - timedelta(days=1),
        purge_at=datetime.now(UTC) + timedelta(days=6),
    )
    token = await make_token(db, user.discord_id)
    client.headers.update({"Authorization": f"Bearer {token}"})

    resp = await client.delete("/api/v1/profile/me/deletion")
    assert resp.status_code == 200

    await db.refresh(user)
    assert user.deletion_requested_at is None
    assert user.purge_at is None


async def test_cancel_deletion_restores_normal_route_access(client, db):
    user = await make_user(
        db,
        discord_id="777777777777777777",
        username="cancel_user2",
        deletion_requested_at=datetime.now(UTC) - timedelta(days=1),
        purge_at=datetime.now(UTC) + timedelta(days=6),
    )
    token = await make_token(db, user.discord_id)
    client.headers.update({"Authorization": f"Bearer {token}"})

    cancel_resp = await client.delete("/api/v1/profile/me/deletion")
    assert cancel_resp.status_code == 200

    resp = await client.get("/api/v1/profile/me")
    assert resp.status_code == 200


async def test_cancel_deletion_unscheduled_account_404s(authed_client):
    resp = await authed_client.delete("/api/v1/profile/me/deletion")
    assert resp.status_code == 404


async def test_schedule_deletion_survives_redis_outage(
    authed_client, db, user, monkeypatch
):
    """A Redis failure during the best-effort link-code flush must not roll back
    or corrupt the already-committed deletion, and must not surface as a 500."""
    import redis.exceptions

    class _BrokenRedis:
        async def getdel(self, *args, **kwargs):
            raise redis.exceptions.ConnectionError("redis unreachable")

    monkeypatch.setattr(
        "app.services.account_deletion.get_redis", lambda: _BrokenRedis()
    )

    resp = await authed_client.post("/api/v1/profile/me/deletion")
    assert resp.status_code == 202
    body = resp.json()

    await db.refresh(user)
    assert user.purge_at is not None
    assert datetime.fromisoformat(body["purge_at"]) == user.purge_at

    result = await db.execute(
        select(UserAuthToken).where(UserAuthToken.user_id == user.discord_id)
    )
    assert result.scalars().all() == []


# ── GET /profile/me/deletion ─────────────────────────────────────────────────

async def test_get_deletion_unscheduled_returns_nulls_with_grace_days(authed_client):
    resp = await authed_client.get("/api/v1/profile/me/deletion")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deletion_requested_at"] is None
    assert body["purge_at"] is None
    assert body["days_left"] is None
    assert body["grace_days"] == settings.account_deletion_grace_days


async def test_get_deletion_pending_returns_the_schedule(db, client):
    """Must not 403 on the pending-deletion guard — it is the endpoint for reading it."""
    user = await make_user(
        db,
        discord_id="888888888888888888",
        username="pending-read",
        deletion_requested_at=datetime.now(UTC) - timedelta(days=2),
        purge_at=datetime.now(UTC) + timedelta(days=5),
    )
    token = await make_token(db, user.discord_id)
    client.headers.update({"Authorization": f"Bearer {token}"})

    resp = await client.get("/api/v1/profile/me/deletion")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deletion_requested_at"] is not None
    assert body["purge_at"] is not None
    assert body["days_left"] == 5
    assert body["grace_days"] == 7


async def test_get_deletion_reports_the_window_the_row_was_scheduled_under(
    db, client, monkeypatch
):
    user = await make_user(
        db,
        discord_id="999999999999999999",
        username="stale-grace",
        deletion_requested_at=datetime.now(UTC) - timedelta(days=30),
        purge_at=datetime.now(UTC) + timedelta(days=5),
    )
    token = await make_token(db, user.discord_id)
    client.headers.update({"Authorization": f"Bearer {token}"})
    monkeypatch.setattr(settings, "account_deletion_grace_days", 7)

    resp = await client.get("/api/v1/profile/me/deletion")

    assert resp.status_code == 200
    assert resp.json()["grace_days"] == 35


async def test_get_deletion_reflects_a_cancel(db, client):
    """The read the web app lacks today: learn a cancel happened on another device."""
    user = await make_user(
        db,
        discord_id="101010101010101010",
        username="cancel-then-read",
        deletion_requested_at=datetime.now(UTC) - timedelta(days=1),
        purge_at=datetime.now(UTC) + timedelta(days=6),
    )
    token = await make_token(db, user.discord_id)
    client.headers.update({"Authorization": f"Bearer {token}"})

    before = await client.get("/api/v1/profile/me/deletion")
    assert before.status_code == 200
    assert before.json()["purge_at"] is not None

    cancelled = await client.delete("/api/v1/profile/me/deletion")
    assert cancelled.status_code == 200

    after = await client.get("/api/v1/profile/me/deletion")
    assert after.status_code == 200
    body = after.json()
    assert body["purge_at"] is None
    assert body["days_left"] is None
    assert body["grace_days"] == settings.account_deletion_grace_days


async def test_get_deletion_requires_a_token(client):
    resp = await client.get("/api/v1/profile/me/deletion")

    assert resp.status_code == 403
