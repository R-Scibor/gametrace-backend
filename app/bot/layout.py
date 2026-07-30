"""Components V2 presentation layer for Discord bot replies.

Pure rendering: builds a `LayoutView` from a title/body pair and picks an
accent colour. No database access, no interaction handling, no Discord
client state — Tasks 3 and 5 own wiring this into views/commands.
"""
from enum import Enum

import discord
from discord.ui import Container, LayoutView, Separator, TextDisplay

from app.bot import replies

_FAILURE_BODIES = frozenset(
    {
        replies.NOT_REGISTERED,
        replies.STATS_FAILURE,
        replies.RECENT_FAILURE,
        replies.LINK_CODES_UNCONFIGURED,
    }
)


class Accent(Enum):
    BRAND = discord.Colour.blurple()
    FAILURE = discord.Colour.red()


def accent_for(body: str) -> Accent:
    if body in _FAILURE_BODIES:
        return Accent.FAILURE
    return Accent.BRAND


def reply_view(title: str, body: str, accent: Accent = Accent.BRAND) -> discord.ui.LayoutView:
    view = LayoutView()
    container = Container(accent_colour=accent.value)
    container.add_item(TextDisplay(f"### {title}"))
    container.add_item(Separator())
    container.add_item(TextDisplay(body))
    view.add_item(container)
    return view
