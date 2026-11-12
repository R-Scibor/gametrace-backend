"""Discord slash command logic — testable without discord.py."""
import logging
from dataclasses import dataclass, field

from sqlalchemy import select

from app.bot import api_client, replies
from app.bot.api_client import BotApiError, PendingDeletionError
from app.core.config import settings
from app.models.user import User, UserAuthToken
from app.services import link_codes
from app.services.account_deletion import days_left as _days_left

logger = logging.getLogger(__name__)


async def _upsert_user(db, discord_id: str, username: str, language: str | None = None) -> bool:
    """Create or sync username. Returns True if a new user was created.

    `language` is written only on insert, and only when it is `pl` or `en`.
    A later call must not change `users.language`.
    """
    user = await db.get(User, discord_id)
    if user is None:
        user = User(discord_id=discord_id, username=username, timezone=settings.default_timezone)
        if language in {"pl", "en"}:
            user.language = language
        db.add(user)
        await db.commit()
        return True
    user.username = username
    await db.commit()
    return False


def _format_code(code: str) -> str:
    return f"{code[:3]} {code[3:]}"


@dataclass(frozen=True)
class LoginCodeFacts:
    unconfigured: bool
    created: bool
    code: str


@dataclass(frozen=True)
class LogoutFacts:
    unregistered: bool
    count: int


@dataclass(frozen=True)
class StatsFacts:
    slash_body: str
    total_seconds: int = 0
    per_game: list = field(default_factory=list)
    pending_errors_count: int = 0
    review_count: int = 0
    raw_failure: str | None = None


@dataclass(frozen=True)
class RecentFacts:
    slash_body: str
    sessions: list = field(default_factory=list)
    user_timezone: str | None = None
    raw_failure: str | None = None


async def register_user(db, discord_id: str, username: str, language: str | None = None) -> str:
    existing = await db.get(User, discord_id)
    pending_deletion = existing is not None and existing.purge_at is not None

    created = await _upsert_user(db, discord_id, username, language)
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


async def issue_login_code_facts(db, r, discord_id: str, username: str) -> LoginCodeFacts:
    created = await _upsert_user(db, discord_id, username)
    try:
        code = await link_codes.issue_code(r, discord_id)
    except link_codes.LinkCodesNotConfigured:
        return LoginCodeFacts(unconfigured=True, created=created, code="")
    return LoginCodeFacts(unconfigured=False, created=created, code=_format_code(code))


async def issue_login_code(db, r, discord_id: str, username: str) -> str:
    facts = await issue_login_code_facts(db, r, discord_id, username)
    if facts.unconfigured:
        return replies.LINK_CODES_UNCONFIGURED
    return replies.login_reply(code=facts.code, created=facts.created)


async def logout_user_facts(db, r, discord_id: str) -> LogoutFacts:
    user = await db.get(User, discord_id)
    if user is None:
        return LogoutFacts(unregistered=True, count=0)

    result = await db.execute(
        select(UserAuthToken).where(UserAuthToken.user_id == discord_id)
    )
    tokens = result.scalars().all()
    count = len(tokens)
    for token in tokens:
        await db.delete(token)
    await db.commit()

    await link_codes.discard_pending_code(r, discord_id)
    return LogoutFacts(unregistered=False, count=count)


async def logout_user(db, r, discord_id: str) -> str:
    facts = await logout_user_facts(db, r, discord_id)
    if facts.unregistered:
        return "Nie jesteś zarejestrowany."
    return f"Wylogowano. Unieważniono {facts.count} tokenów logowania w aplikacji."


async def stats_facts(db, discord_id: str) -> StatsFacts:
    """Read-only /stats payload. Never creates a users row."""
    user = await db.get(User, discord_id)
    if user is None:
        return StatsFacts(slash_body=replies.NOT_REGISTERED, raw_failure=replies.NOT_REGISTERED)

    try:
        summary = await api_client.get_summary(discord_id)
        review_count = await api_client.get_review_count(discord_id)
    except PendingDeletionError as exc:
        # Must be caught before BotApiError — PendingDeletionError is a
        # subclass, so a blanket `except BotApiError` here would also catch
        # it and mislabel it as a generic failure.
        body = replies.pending_deletion_reply(
            purge_at=exc.purge_at, days_left=exc.days_left, user_timezone=user.timezone
        )
        return StatsFacts(slash_body=body, raw_failure=body)
    except BotApiError:
        logger.warning("Failed to fetch /stats data for %s", discord_id, exc_info=True)
        return StatsFacts(slash_body=replies.STATS_FAILURE, raw_failure=replies.STATS_FAILURE)

    total_seconds = summary.get("total_seconds", 0)
    per_game = summary.get("per_game", [])
    pending_errors_count = len(summary.get("pending_errors", []))
    return StatsFacts(
        slash_body=replies.stats_reply(
            total_seconds=total_seconds,
            per_game=per_game,
            pending_errors_count=pending_errors_count,
            review_count=review_count,
        ),
        total_seconds=total_seconds,
        per_game=per_game,
        pending_errors_count=pending_errors_count,
        review_count=review_count,
    )


async def stats_command(db, discord_id: str) -> str:
    """Build the /stats reply. Read-only — never creates a users row.

    Caller (app/bot/main.py) is responsible for deferring the interaction
    before invoking this, since the API round-trip can exceed Discord's
    ~3s ack deadline.
    """
    facts = await stats_facts(db, discord_id)
    return facts.slash_body


def help_command() -> str:
    """Build the /help reply. No HTTP call, no DB lookup — reply directly."""
    return replies.help_reply()


async def recent_facts(db, discord_id: str) -> RecentFacts:
    """Read-only /recent payload. Never creates a users row."""
    user = await db.get(User, discord_id)
    if user is None:
        return RecentFacts(slash_body=replies.NOT_REGISTERED, raw_failure=replies.NOT_REGISTERED)

    try:
        sessions = await api_client.get_recent_sessions(discord_id)
    except PendingDeletionError as exc:
        # Same catch-order note as stats_facts: subclass before base class.
        body = replies.pending_deletion_reply(
            purge_at=exc.purge_at, days_left=exc.days_left, user_timezone=user.timezone
        )
        return RecentFacts(slash_body=body, raw_failure=body)
    except BotApiError:
        logger.warning("Failed to fetch /recent data for %s", discord_id, exc_info=True)
        return RecentFacts(slash_body=replies.RECENT_FAILURE, raw_failure=replies.RECENT_FAILURE)

    return RecentFacts(
        slash_body=replies.recent_reply(sessions=sessions, user_timezone=user.timezone),
        sessions=sessions,
        user_timezone=user.timezone,
    )


async def recent_command(db, discord_id: str) -> str:
    """Build the /recent reply. Read-only — never creates a users row.

    Caller (app/bot/main.py) is responsible for deferring the interaction
    before invoking this, since the API round-trip can exceed Discord's
    ~3s ack deadline.
    """
    facts = await recent_facts(db, discord_id)
    return facts.slash_body
