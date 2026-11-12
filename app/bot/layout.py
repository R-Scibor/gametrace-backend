"""Components V2 presentation layer for Discord bot replies.

Pure rendering: builds a `LayoutView` from a title/body pair and picks an
accent colour. No database access, no interaction handling, no Discord
client state — `app/bot/main.py` and `app/bot/panel.py` wire this into
slash-command replies and panel views.
"""
from enum import Enum

import discord
from discord.enums import SeparatorSpacing
from discord.ui import Container, LayoutView, Separator, TextDisplay

from app.bot import replies

_FAILURE_BODIES = frozenset(
    {
        replies.NOT_REGISTERED,
        replies.STATS_FAILURE,
        replies.RECENT_FAILURE,
        replies.LINK_CODES_UNCONFIGURED,
        replies.LINK_CODES_UNCONFIGURED_EN,
        replies.MENU_ACTION_FAILED,
        replies.MENU_ACTION_FAILED_EN,
        replies.PANEL_MISSING_PERMISSIONS,
        replies.PANEL_CHANNEL_UNAVAILABLE,
        replies.PANEL_REFUSED_NOT_ADMIN,
    }
)


class Accent(Enum):
    BRAND = discord.Colour(0xFF7A1A)
    FAILURE = discord.Colour.red()


def accent_for(body: str) -> Accent:
    if body in _FAILURE_BODIES:
        return Accent.FAILURE
    return Accent.BRAND


def card_view(
    *,
    title: str,
    body: str,
    accent: Accent = Accent.BRAND,
    caption: str | None = None,
    row=None,
    buttons_disabled: bool = False,
) -> discord.ui.LayoutView:
    heading = f"### {title}" if caption is None else f"### {title}\n-# {caption}"
    view = LayoutView(timeout=None)
    container: Container[LayoutView] = Container(accent_colour=accent.value)
    container.add_item(TextDisplay(heading))
    container.add_item(Separator(visible=False, spacing=SeparatorSpacing.small))
    container.add_item(TextDisplay(body))
    container.add_item(Separator(visible=True, spacing=SeparatorSpacing.large))
    if row is not None:
        if buttons_disabled:
            for child in row.children:
                child.disabled = True
        container.add_item(row)
    view.add_item(container)
    return view


def reply_view(title: str, body: str, accent: Accent = Accent.BRAND) -> discord.ui.LayoutView:
    view = LayoutView()
    container: Container[LayoutView] = Container(accent_colour=accent.value)
    container.add_item(TextDisplay(f"### {title}"))
    container.add_item(Separator())
    container.add_item(TextDisplay(body))
    view.add_item(container)
    return view
