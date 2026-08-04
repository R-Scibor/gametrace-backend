"""Discord slash command logic — testable without discord.py."""
import logging
import math
from datetime import datetime, timezone

from sqlalchemy import select

from app.bot import api_client, replies
from app.bot.api_client import BotApiError, PendingDeletionError
from app.core.config import settings
from app.models.user import User, UserAuthToken
from app.services import link_codes

logger = logging.getLogger(__name__)


def _days_left(purge_at: datetime) -> int:
    """Ceiling of the time remaining until purge_at, in whole days. Mirrors
    `_days_left` in app/api/v1/endpoints/auth.py — duplicated rather than
    imported since the bot and API are separate deployables; both must stay
    in sync if the rounding rule ever changes.
    """
    now = datetime.now(timezone.utc)
    delta = purge_at - now
    return max(1, math.ceil(delta.total_seconds() / 86400))


async def _upsert_user(db, discord_id: str, username: str) -> bool:
    """Create or sync username. Returns True if a new user was created."""
    user = await db.get(User, discord_id)
    if user is None:
        user = User(discord_id=discord_id, username=username, timezone=settings.default_timezone)
        db.add(user)
        await db.commit()
        return True
    user.username = username
    await db.commit()
    return False


def _format_code(code: str) -> str:
    return f"{code[:3]} {code[3:]}"


async def register_user(db, discord_id: str, username: str) -> str:
    existing = await db.get(User, discord_id)
    pending_deletion = existing is not None and existing.purge_at is not None

    created = await _upsert_user(db, discord_id, username)
    if created:
        logger.info("New user registered via /register: %s (%s)", username, discord_id)
    else:
        logger.info("Existing user /register: %s (%s)", username, discord_id)

    if pending_deletion:
        # Account row still exists, so _upsert_user reports created=False —
        # but "already registered" would be actively misleading for an
        # account queued for erasure. existing.purge_at/deletion_requested_at
        # are pre-upsert values; _upsert_user only ever touches username.
        return replies.register_pending_deletion_reply(
            purge_at=existing.purge_at.isoformat(),
            days_left=_days_left(existing.purge_at),
            user_timezone=existing.timezone,
        )

    return replies.register_reply(created=created)


async def issue_login_code(db, r, discord_id: str, username: str) -> str:
    try:
        created = await _upsert_user(db, discord_id, username)
        code = await link_codes.issue_code(r, discord_id)
    except link_codes.LinkCodesNotConfigured:
        return replies.LINK_CODES_UNCONFIGURED

    spaced = _format_code(code)
    return replies.login_reply(code=spaced, created=created)


async def logout_user(db, r, discord_id: str) -> str:
    user = await db.get(User, discord_id)
    if user is None:
        return "Nie jesteś zarejestrowany."

    result = await db.execute(
        select(UserAuthToken).where(UserAuthToken.user_id == discord_id)
    )
    tokens = result.scalars().all()
    count = len(tokens)
    for token in tokens:
        await db.delete(token)
    await db.commit()

    await link_codes.discard_pending_code(r, discord_id)
    return f"Wylogowano. Unieważniono {count} tokenów logowania w aplikacji."


async def stats_command(db, discord_id: str) -> str:
    """Build the /stats reply. Read-only — never creates a users row.

    Caller (app/bot/main.py) is responsible for deferring the interaction
    before invoking this, since the API round-trip can exceed Discord's
    ~3s ack deadline.
    """
    user = await db.get(User, discord_id)
    if user is None:
        return replies.NOT_REGISTERED

    try:
        summary = await api_client.get_summary(discord_id)
        review_count = await api_client.get_review_count(discord_id)
    except PendingDeletionError as exc:
        # Must be caught before BotApiError — PendingDeletionError is a
        # subclass, so a blanket `except BotApiError` here would also catch
        # it and mislabel it as a generic failure.
        return replies.pending_deletion_reply(
            purge_at=exc.purge_at, days_left=exc.days_left, user_timezone=user.timezone
        )
    except BotApiError:
        logger.warning("Failed to fetch /stats data for %s", discord_id, exc_info=True)
        return replies.STATS_FAILURE

    return replies.stats_reply(
        total_seconds=summary.get("total_seconds", 0),
        per_game=summary.get("per_game", []),
        pending_errors_count=len(summary.get("pending_errors", [])),
        review_count=review_count,
    )


def help_command() -> str:
    """Build the /help reply. No HTTP call, no DB lookup — reply directly."""
    return replies.help_reply()


async def recent_command(db, discord_id: str) -> str:
    """Build the /recent reply. Read-only — never creates a users row.

    Caller (app/bot/main.py) is responsible for deferring the interaction
    before invoking this, since the API round-trip can exceed Discord's
    ~3s ack deadline.
    """
    user = await db.get(User, discord_id)
    if user is None:
        return replies.NOT_REGISTERED

    try:
        sessions = await api_client.get_recent_sessions(discord_id)
    except PendingDeletionError as exc:
        # Same catch-order note as stats_command: subclass before base class.
        return replies.pending_deletion_reply(
            purge_at=exc.purge_at, days_left=exc.days_left, user_timezone=user.timezone
        )
    except BotApiError:
        logger.warning("Failed to fetch /recent data for %s", discord_id, exc_info=True)
        return replies.RECENT_FAILURE

    return replies.recent_reply(sessions=sessions, user_timezone=user.timezone)
