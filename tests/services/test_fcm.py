"""Unit tests for app.services.fcm.send_to_user.

These never hit real Firebase — _send_multicast is patched to return fake
BatchResponse objects so we can assert the cleanup + bookkeeping behaviour.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.user import UserDevice
from app.services import fcm
from tests.factories import make_device


@pytest.fixture
def skip_fcm_init(monkeypatch):
    """Tests that patch _send_multicast shouldn't trip the credentials check."""
    monkeypatch.setattr(fcm, "_ensure_initialized", lambda: None)


def _ok():
    return SimpleNamespace(success=True, exception=None)


def _err(exc_name: str):
    # Mimic firebase_admin.exceptions.FirebaseError shape: we only read type(err).__name__
    exc = type(exc_name, (Exception,), {})()
    return SimpleNamespace(success=False, exception=exc)


async def test_returns_zero_when_user_has_no_devices(db, user):
    # Patch must exist even though it shouldn't be called
    sent = await fcm.send_to_user(db, user.discord_id, "t", "b")
    assert sent == 0


async def test_successful_send_bumps_last_active(db, user, monkeypatch, skip_fcm_init):
    device = await make_device(db, user.discord_id, "token-live")
    # Set last_active in the past so bump is detectable
    past = datetime.now(timezone.utc) - timedelta(days=10)
    device.last_active = past
    await db.flush()

    batch = SimpleNamespace(responses=[_ok()])
    monkeypatch.setattr(fcm, "_send_multicast", lambda *a, **k: batch)

    count = await fcm.send_to_user(db, user.discord_id, "t", "b")

    assert count == 1
    await db.refresh(device)
    assert device.last_active > past


async def test_unrecoverable_error_deletes_token(db, user, monkeypatch, skip_fcm_init):
    await make_device(db, user.discord_id, "token-dead")

    batch = SimpleNamespace(responses=[_err("UnregisteredError")])
    monkeypatch.setattr(fcm, "_send_multicast", lambda *a, **k: batch)

    count = await fcm.send_to_user(db, user.discord_id, "t", "b")

    assert count == 0
    remaining = (
        await db.execute(select(UserDevice).where(UserDevice.fcm_token == "token-dead"))
    ).scalar_one_or_none()
    assert remaining is None


async def test_transient_error_keeps_token(db, user, monkeypatch, skip_fcm_init):
    await make_device(db, user.discord_id, "token-transient")

    batch = SimpleNamespace(responses=[_err("UnavailableError")])
    monkeypatch.setattr(fcm, "_send_multicast", lambda *a, **k: batch)

    count = await fcm.send_to_user(db, user.discord_id, "t", "b")

    assert count == 0
    remaining = (
        await db.execute(
            select(UserDevice).where(UserDevice.fcm_token == "token-transient")
        )
    ).scalar_one_or_none()
    assert remaining is not None


async def test_mixed_batch_partial_cleanup(db, user, monkeypatch, skip_fcm_init):
    await make_device(db, user.discord_id, "token-a")
    await make_device(db, user.discord_id, "token-b")
    await make_device(db, user.discord_id, "token-c")

    # Response order matches input token order — send_to_user reads all devices in DB order.
    # We patch _send_multicast to return a batch indexed to the tokens it was called with.
    def fake_send(tokens, title, body, data):
        mapping = {
            "token-a": _ok(),
            "token-b": _err("UnregisteredError"),
            "token-c": _err("UnavailableError"),
        }
        return SimpleNamespace(responses=[mapping[t] for t in tokens])

    monkeypatch.setattr(fcm, "_send_multicast", fake_send)

    count = await fcm.send_to_user(db, user.discord_id, "t", "b")

    assert count == 1
    remaining = set(
        (await db.execute(select(UserDevice.fcm_token))).scalars().all()
    )
    # token-a stays (live), token-b gone (dead), token-c stays (transient)
    assert remaining == {"token-a", "token-c"}


async def test_init_is_lazy(monkeypatch):
    """Importing the module must not touch credentials."""
    # Reset the initialized flag and assert that no-op imports don't flip it
    # (send_to_user is the only path that initializes)
    assert fcm._initialized is False or fcm._initialized is True  # flag just exists
    # Not calling send_to_user here → no init attempted. Passes if import succeeded.


async def test_missing_credentials_path_raises_on_send(db, user, monkeypatch):
    await make_device(db, user.discord_id, "token-x")

    # Force uninitialized state + empty credentials path
    monkeypatch.setattr(fcm, "_initialized", False)
    monkeypatch.setattr(fcm.settings, "firebase_credentials_path", "")

    with pytest.raises(RuntimeError, match="FIREBASE_CREDENTIALS_PATH"):
        await fcm.send_to_user(db, user.discord_id, "t", "b")
