"""Firebase Cloud Messaging helper.

Lazy init so module import never touches the filesystem — tests patch
``_send_multicast`` and never need real credentials.

``send_to_user`` handles dead-token cleanup inline (not just via the Celery
sweeper) so the weekly fan-out doesn't keep retrying uninstalled devices.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import UserDevice

logger = logging.getLogger(__name__)

# Error types that mean the token is permanently dead — delete it.
_DEAD_TOKEN_ERROR_NAMES = {
    "UnregisteredError",
    "SenderIdMismatchError",
    "InvalidArgumentError",
}

_init_lock = Lock()
_initialized = False


def _ensure_initialized() -> None:
    """Initialize the firebase_admin app once, on first send."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        if not settings.firebase_credentials_path:
            raise RuntimeError(
                "FIREBASE_CREDENTIALS_PATH not configured — cannot send FCM notifications"
            )
        import firebase_admin
        from firebase_admin import credentials

        if not firebase_admin._apps:
            cred = credentials.Certificate(settings.firebase_credentials_path)
            firebase_admin.initialize_app(cred)
        _initialized = True


def _send_multicast(tokens: list[str], title: str, body: str, data: dict[str, str]):
    """Thin wrapper around firebase_admin.messaging so tests can patch it."""
    from firebase_admin import messaging

    message = messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=title, body=body),
        data=data,
    )
    return messaging.send_each_for_multicast(message)


async def send_to_user(
    db: AsyncSession,
    user_id: str,
    title: str,
    body: str,
    data: Optional[dict[str, str]] = None,
) -> int:
    """
    Send a notification to every registered device for one user.

    Returns the number of successful deliveries. Unrecoverable-error tokens
    are deleted from user_devices; successful-delivery tokens have
    last_active bumped. Transient errors are left alone.
    """
    data = data or {}

    rows = (
        await db.execute(
            select(UserDevice).where(UserDevice.user_id == user_id)
        )
    ).scalars().all()

    if not rows:
        return 0

    _ensure_initialized()

    tokens = [d.fcm_token for d in rows]
    batch = _send_multicast(tokens, title, body, data)

    dead_tokens: list[str] = []
    live_tokens: list[str] = []

    for token, response in zip(tokens, batch.responses):
        if response.success:
            live_tokens.append(token)
            continue
        err = response.exception
        err_name = type(err).__name__ if err is not None else ""
        if err_name in _DEAD_TOKEN_ERROR_NAMES:
            dead_tokens.append(token)
            logger.info("Deleting dead FCM token (err=%s)", err_name)
        else:
            logger.warning("Transient FCM error for one token: %s", err_name or err)

    if dead_tokens:
        await db.execute(
            delete(UserDevice).where(UserDevice.fcm_token.in_(dead_tokens))
        )

    if live_tokens:
        now = datetime.now(timezone.utc)
        for device in rows:
            if device.fcm_token in live_tokens:
                device.last_active = now

    await db.commit()
    return len(live_tokens)
