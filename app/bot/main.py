"""
Discord bot — Phase 2.

Requires PRESENCE_INTENT enabled in Discord Developer Portal.
Tracks game sessions for users who have logged into the app (exist in users table).
"""
import logging
import time

import discord
import structlog
from discord import app_commands
from discord.ext import tasks

from app.bot import layout, replies
from app.bot.panel import PERSISTENT_VIEWS, PanelView
from app.core.config import settings
from app.core.redis import get_redis
from app.core.database import AsyncSessionLocal
from app.core.logging import configure_logging, new_trace_id
from app.core.observability import init_sentry

logger = logging.getLogger(__name__)

BOT_STARTED_AT_KEY = "bot:started_at"
BOT_HEARTBEAT_KEY = "bot:heartbeat"
HEARTBEAT_TTL_SECONDS = 90

@tasks.loop(seconds=30)
async def _heartbeat_loop() -> None:
    try:
        await get_redis().set(BOT_HEARTBEAT_KEY, int(time.time()), ex=HEARTBEAT_TTL_SECONDS)
    except Exception:
        logger.warning("Heartbeat write to Redis failed", exc_info=True)

intents = discord.Intents.default()
intents.presences = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# `on_ready` re-fires on every gateway reconnect, not just process start.
# Registering the same persistent views again on each reconnect would be
# harmless functionally (same custom_ids, same classes) but is still
# needless repeated work — guard it the same way `_heartbeat_loop.is_running()`
# guards the heartbeat loop below.
_views_registered = False


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
    global _views_registered
    logger.info("Bot connected as %s", bot.user)
    await tree.sync()
    logger.info("Slash commands synced.")
    synced_guilds = 0
    for guild_id in settings.discord_guild_id_set:
        try:
            guild_obj = discord.Object(id=int(guild_id))
            tree.copy_global_to(guild=guild_obj)
            await tree.sync(guild=guild_obj)
            synced_guilds += 1
        except Exception:
            logger.warning("Guild command sync failed for guild_id=%s", guild_id, exc_info=True)
    logger.info("Slash commands synced to %d guild(s).", synced_guilds)
    if not _views_registered:
        for view_cls in PERSISTENT_VIEWS:
            bot.add_view(view_cls())
        _views_registered = True
        logger.info("Persistent panel views registered.")
    try:
        await get_redis().set(BOT_STARTED_AT_KEY, int(time.time()))
    except Exception:
        logger.warning("Failed to write bot:started_at to Redis", exc_info=True)
    if not _heartbeat_loop.is_running():
        _heartbeat_loop.start()
    async with AsyncSessionLocal() as db:
        from app.bot.self_healing import run_self_healing
        await run_self_healing(db, bot.guilds)


@tree.command(name="register", description="Zarejestruj się w GameTrace")
async def register_command(interaction: discord.Interaction) -> None:
    discord_id = str(interaction.user.id)
    username = interaction.user.name

    async with AsyncSessionLocal() as db:
        from app.bot.commands import register_user

        msg = await register_user(db, discord_id, username)

    await interaction.response.send_message(
        view=layout.reply_view("Rejestracja", msg, layout.accent_for(msg)), ephemeral=True
    )


@tree.command(name="login", description="Uzyskaj kod logowania do aplikacji GameTrace")
async def login_command(interaction: discord.Interaction) -> None:
    discord_id = str(interaction.user.id)
    username = interaction.user.name

    async with AsyncSessionLocal() as db:
        from app.bot.commands import issue_login_code

        msg = await issue_login_code(db, get_redis(), discord_id, username)

    await interaction.response.send_message(
        view=layout.reply_view("Kod logowania", msg, layout.accent_for(msg)), ephemeral=True
    )


@tree.command(name="logout", description="Wyloguj się ze wszystkich urządzeń w aplikacji GameTrace")
async def logout_command(interaction: discord.Interaction) -> None:
    discord_id = str(interaction.user.id)

    async with AsyncSessionLocal() as db:
        from app.bot.commands import logout_user

        msg = await logout_user(db, get_redis(), discord_id)

    await interaction.response.send_message(
        view=layout.reply_view("Wylogowano", msg, layout.accent_for(msg)), ephemeral=True
    )


@tree.command(name="stats", description="Łączny czas grania i najczęściej grane gry z ostatnich 7 dni")
async def stats_command_handler(interaction: discord.Interaction) -> None:
    # Defer before any I/O — a cold container's DB query + API round-trip can
    # exceed Discord's ~3s ack deadline, otherwise showing a false failure.
    await interaction.response.defer(ephemeral=True)
    discord_id = str(interaction.user.id)

    async with AsyncSessionLocal() as db:
        from app.bot.commands import stats_command

        msg = await stats_command(db, discord_id)

    await interaction.followup.send(
        view=layout.reply_view("Statystyki", msg, layout.accent_for(msg)), ephemeral=True
    )


@tree.command(name="recent", description="Ostatnie sesje: gra, kiedy grałeś i ile trwało")
async def recent_command_handler(interaction: discord.Interaction) -> None:
    # Defer before any I/O — same rationale as /stats.
    await interaction.response.defer(ephemeral=True)
    discord_id = str(interaction.user.id)

    async with AsyncSessionLocal() as db:
        from app.bot.commands import recent_command

        msg = await recent_command(db, discord_id)

    await interaction.followup.send(
        view=layout.reply_view("Ostatnie sesje", msg, layout.accent_for(msg)), ephemeral=True
    )


@tree.command(name="help", description="Czym jest GameTrace i jak zacząć")
async def help_command_handler(interaction: discord.Interaction) -> None:
    # No I/O — no defer, no registration check, unlike /stats and /recent.
    from app.bot.commands import help_command

    msg = help_command()

    await interaction.response.send_message(
        view=layout.reply_view("GameTrace", msg, layout.accent_for(msg)), ephemeral=True
    )


async def _panel_ack(interaction: discord.Interaction, body: str) -> None:
    """Ephemeral V2 reply for the /panel command itself — same rendering
    path (`layout.reply_view` + `layout.accent_for`) as every other slash
    command's reply, so PANEL_POSTED/PANEL_MISSING_PERMISSIONS/
    PANEL_REFUSED_NOT_ADMIN are never sent as bare positional strings."""
    await interaction.response.send_message(
        view=layout.reply_view("Panel", body, layout.accent_for(body)),
        ephemeral=True,
    )


@tree.command(
    name="panel",
    description="Opublikuj panel startowy GameTrace na tym kanale (dla adminów)",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def panel_command(interaction: discord.Interaction) -> None:
    # Discord's own manage_guild permission gates who can post the panel —
    # this is a "who may post in this channel" question, not GameTrace RBAC,
    # so there is deliberately no is_admin/database lookup here.
    if interaction.channel is None:
        # guild_only() makes this unlikely in practice, but interaction.channel
        # can still be None (e.g. an uncached channel) — send() on None would
        # raise AttributeError, which the Forbidden handler below can't catch.
        # Not a permissions problem — say so, or an admin goes hunting for a
        # Send Messages grant that is already in place.
        await _panel_ack(interaction, replies.PANEL_CHANNEL_UNAVAILABLE)
        return

    try:
        await interaction.channel.send(view=PanelView())
    except discord.Forbidden:
        # Realistic failure: a locked channel where the bot itself also
        # lacks Send Messages. Silent failure here is maddening to debug.
        await _panel_ack(interaction, replies.PANEL_MISSING_PERMISSIONS)
        return

    await _panel_ack(interaction, replies.PANEL_POSTED)


@panel_command.error
async def panel_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        await _panel_ack(interaction, replies.PANEL_REFUSED_NOT_ADMIN)
        return
    raise error


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

    with structlog.contextvars.bound_contextvars(trace_id=new_trace_id()):
        from app.bot.session_lock import user_session_lock

        async with AsyncSessionLocal() as db:
            async with user_session_lock(db, discord_id):
                from app.bot.session_manager import (
                    complete_session,
                    error_session,
                    get_ongoing_session,
                    get_or_create_game,
                    get_user_if_tracked,
                    start_or_resume_session,
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
                    # Game started. Discord sometimes redelivers a start with an
                    # empty `before`, so this fires while the same game is still
                    # ONGOING — the equality guard above misses it. Resolve the
                    # game first: if it matches the ONGOING session, this is a
                    # spurious repeat, leave the session running. Only a stale
                    # ONGOING for a *different* game is an orphan worth erroring.
                    game, created = await get_or_create_game(db, after_game)
                    if ongoing and ongoing.game_id == game.id:
                        pass
                    else:
                        if ongoing:
                            await error_session(
                                db,
                                ongoing,
                                f"Self-Healing: unexpected ONGOING session when new game {after_game!r} started.",
                            )
                        await start_or_resume_session(db, discord_id, game.id)
                        if created:
                            _queue_enrichment(game.id)

                elif before_game and after_game:
                    # Switched game — complete old, start new
                    if ongoing:
                        await complete_session(db, ongoing)
                    game, created = await get_or_create_game(db, after_game)
                    await start_or_resume_session(db, discord_id, game.id)
                    if created:
                        _queue_enrichment(game.id)


def _queue_enrichment(game_id: int) -> None:
    """Fire-and-forget enrichment task. Redis deduplication via fixed task ID."""
    try:
        from app.services.enrichment_dispatch import queue_enrichment

        queue_enrichment(game_id)
    except Exception:
        # Never crash the bot over a background task failure
        logger.exception("Failed to queue enrichment for game_id=%d", game_id)


if __name__ == "__main__":
    configure_logging(settings.log_component or "bot", settings.log_level)
    init_sentry("bot")
    if not settings.discord_bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set")
    bot.run(settings.discord_bot_token)
