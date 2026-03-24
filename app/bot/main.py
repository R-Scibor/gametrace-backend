"""
Discord bot — Phase 2 implementation.

Requires PRESENCE_INTENT enabled in Discord Developer Portal.
"""
import logging

import discord

from app.core.config import settings

logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.presences = True
intents.members = True

bot = discord.Client(intents=intents)


@bot.event
async def on_ready():
    logger.info("Bot connected as %s", bot.user)


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    # Phase 2: detect game start/stop and write game_sessions
    pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not settings.discord_bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set")
    bot.run(settings.discord_bot_token)
