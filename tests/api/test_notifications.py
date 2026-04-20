from sqlalchemy import select

from app.models.user import UserDevice
from tests.factories import make_device, make_token, make_user


async def test_register_new_token(authed_client, db, user):
    resp = await authed_client.post(
        "/api/v1/notifications/register-token",
        json={"fcm_token": "tok-new", "device_type": "android"},
    )

    assert resp.status_code == 200
    row = (
        await db.execute(select(UserDevice).where(UserDevice.fcm_token == "tok-new"))
    ).scalar_one()
    assert row.user_id == user.discord_id
    assert row.device_type == "android"


async def test_register_same_token_twice_no_duplicate(authed_client, db, user):
    body = {"fcm_token": "tok-x", "device_type": "android"}
    await authed_client.post("/api/v1/notifications/register-token", json=body)
    await authed_client.post(
        "/api/v1/notifications/register-token",
        json={"fcm_token": "tok-x", "device_type": "ios"},  # different device_type
    )

    rows = (
        await db.execute(select(UserDevice).where(UserDevice.fcm_token == "tok-x"))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].device_type == "ios"  # last write wins


async def test_token_reassigns_between_users(client, db):
    user_a = await make_user(db, discord_id="100000000000000001", username="alice")
    user_b = await make_user(db, discord_id="100000000000000002", username="bob")
    token_a = await make_token(db, user_a.discord_id)
    token_b = await make_token(db, user_b.discord_id)

    body = {"fcm_token": "shared-device", "device_type": "android"}

    # Alice registers the device first
    client.headers["Authorization"] = f"Bearer {token_a}"
    resp_a = await client.post("/api/v1/notifications/register-token", json=body)
    assert resp_a.status_code == 200

    # Bob logs in on the same physical device, registers the same token
    client.headers["Authorization"] = f"Bearer {token_b}"
    resp_b = await client.post("/api/v1/notifications/register-token", json=body)
    assert resp_b.status_code == 200

    # Exactly one row, now owned by Bob
    rows = (
        await db.execute(
            select(UserDevice).where(UserDevice.fcm_token == "shared-device")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].user_id == user_b.discord_id


async def test_unregister_existing_token(authed_client, db, user):
    await make_device(db, user.discord_id, "tok-del")

    resp = await authed_client.request(
        "DELETE",
        "/api/v1/notifications/register-token",
        json={"fcm_token": "tok-del"},
    )

    assert resp.status_code == 204
    remaining = (
        await db.execute(select(UserDevice).where(UserDevice.fcm_token == "tok-del"))
    ).scalar_one_or_none()
    assert remaining is None


async def test_unregister_missing_token_is_idempotent(authed_client):
    resp = await authed_client.request(
        "DELETE",
        "/api/v1/notifications/register-token",
        json={"fcm_token": "never-existed"},
    )

    assert resp.status_code == 204


async def test_unregister_only_deletes_own_tokens(client, db):
    user_a = await make_user(db, discord_id="200000000000000001", username="alice2")
    user_b = await make_user(db, discord_id="200000000000000002", username="bob2")
    token_b = await make_token(db, user_b.discord_id)
    await make_device(db, user_a.discord_id, "alice-device")

    # Bob tries to unregister Alice's token — no-op, not a breach
    client.headers["Authorization"] = f"Bearer {token_b}"
    resp = await client.request(
        "DELETE",
        "/api/v1/notifications/register-token",
        json={"fcm_token": "alice-device"},
    )

    assert resp.status_code == 204
    still_there = (
        await db.execute(
            select(UserDevice).where(UserDevice.fcm_token == "alice-device")
        )
    ).scalar_one_or_none()
    assert still_there is not None


async def test_register_requires_auth(client):
    resp = await client.post(
        "/api/v1/notifications/register-token",
        json={"fcm_token": "nope", "device_type": "android"},
    )

    assert resp.status_code == 403
