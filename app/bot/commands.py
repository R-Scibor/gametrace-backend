"""Discord slash command logic — testable without discord.py."""
import logging

from sqlalchemy import select

from app.bot import api_client, replies
from app.bot.api_client import BotApiError
from app.core.config import settings
from app.models.user import User, UserAuthToken
from app.services import link_codes

logger = logging.getLogger(__name__)


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
    created = await _upsert_user(db, discord_id, username)
    if created:
        logger.info("New user registered via /register: %s (%s)", username, discord_id)
        return "Zarejestrowano w GameTrace!"
    logger.info("Existing user /register: %s (%s)", username, discord_id)
    return "Już jesteś zarejestrowany."


async def issue_login_code(db, r, discord_id: str, username: str) -> str:
    try:
        await _upsert_user(db, discord_id, username)
        code = await link_codes.issue_code(r, discord_id)
    except link_codes.LinkCodesNotConfigured:
        return "Kody logowania nie są skonfigurowane."

    spaced = _format_code(code)
    return f"Twój kod logowania: **{spaced}**. Wpisz go w aplikacji w ciągu 5 minut."


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
    return f"Wylogowano. Unieważniono {count} sesji w aplikacji."


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
    except BotApiError:
        logger.warning("Failed to fetch /stats data for %s", discord_id, exc_info=True)
        return replies.STATS_FAILURE

    return replies.stats_reply(
        total_seconds=summary.get("total_seconds", 0),
        per_game=summary.get("per_game", []),
        pending_errors_count=len(summary.get("pending_errors", [])),
        review_count=review_count,
    )
