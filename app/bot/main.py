"""
Discord bot — Phase 2.

Requires PRESENCE_INTENT enabled in Discord Developer Portal.
Tracks game sessions for users who have logged into the app (exist in users table).
"""
import logging

import discord
from discord import app_commands

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.observability import init_sentry

logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.presences = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


def _get_game_name(member: discord.Member) -> str | None:
    """Extract the currently played game name from a member's activities."""
    for activity in member.activities:
        if isinstance(activity, discord.Game):
            return activity.name
        if (
            isinstance(activity, discord.Activity)
            and activity.type == discord.ActivityType.playing
        ):
            return activity.name
    return None


@bot.event
async def on_ready():
    logger.info("Bot connected as %s", bot.user)
    await tree.sync()
    logger.info("Slash commands synced.")
    async with AsyncSessionLocal() as db:
        from app.bot.self_healing import run_self_healing
        await run_self_healing(db, bot.guilds)


@tree.command(name="login", description="Zarejestruj się w GameTrace")
async def login_command(interaction: discord.Interaction) -> None:
    """Rejestruje użytkownika w bazie GameTrace na podstawie jego Discord ID i username."""
    discord_id = str(interaction.user.id)
    username = interaction.user.name

    async with AsyncSessionLocal() as db:
        from app.models.user import User
        user = await db.get(User, discord_id)
        if user is None:
            user = User(discord_id=discord_id, username=username)
            db.add(user)
            await db.commit()
            msg = "Zarejestrowano w GameTrace! Zaloguj się w aplikacji swoją nazwą Discord."
            logger.info("New user registered via /login: %s (%s)", username, discord_id)
        else:
            user.username = username  # sync in case Discord username changed
            await db.commit()
            msg = "Już jesteś zarejestrowany. Zaloguj się w aplikacji swoją nazwą Discord."
            logger.info("Existing user /login: %s (%s)", username, discord_id)

    await interaction.response.send_message(msg, ephemeral=True)


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    # Ignore bots
    if after.bot:
        return

    before_game = _get_game_name(before)
    after_game = _get_game_name(after)

    # No change in game status — nothing to do
    if before_game == after_game:
        return

    discord_id = str(after.id)

    async with AsyncSessionLocal() as db:
        from app.bot.session_manager import (
            complete_session,
            error_session,
            get_ongoing_session,
            get_or_create_game,
            get_user_if_tracked,
            start_session,
        )

        user = await get_user_if_tracked(db, discord_id)
        if user is None:
            # User has never logged into the app — bot ignores them
            return

        ongoing = await get_ongoing_session(db, discord_id)

        if before_game and not after_game:
            # Game closed — complete the ongoing session
            if ongoing:
                await complete_session(db, ongoing)

        elif not before_game and after_game:
            # Game started — close any stale session just in case, then start new
            if ongoing:
                await error_session(
                    db,
                    ongoing,
                    f"Self-Healing: unexpected ONGOING session when new game {after_game!r} started.",
                )
            game, created = await get_or_create_game(db, after_game)
            await start_session(db, discord_id, game.id)
            if created:
                _queue_enrichment(game.id)

        elif before_game and after_game:
            # Switched game — complete old, start new
            if ongoing:
                await complete_session(db, ongoing)
            game, created = await get_or_create_game(db, after_game)
            await start_session(db, discord_id, game.id)
            if created:
                _queue_enrichment(game.id)


def _queue_enrichment(game_id: int) -> None:
    """Fire-and-forget enrichment task. Redis deduplication via fixed task ID."""
    try:
        from app.tasks.enrichment import enrich_game
        task_id = f"enrich_game_{game_id}"
        enrich_game.apply_async(args=[game_id], task_id=task_id)
    except Exception:
        # Never crash the bot over a background task failure
        logger.exception("Failed to queue enrichment for game_id=%d", game_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_sentry("bot")
    if not settings.discord_bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set")
    bot.run(settings.discord_bot_token)
