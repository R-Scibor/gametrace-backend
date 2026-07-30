"""V2 layout helpers — container rendering and accent selection.

No DB, no interaction handling: layout.py is pure presentation.
"""
import discord
from discord.ui import TextDisplay

from app.bot import replies
from app.bot.layout import Accent, accent_for, reply_view


def _text_displays(view):
    """Walk the view's containers and collect TextDisplay content strings."""
    contents = []
    for item in view.children:
        children = getattr(item, "children", [item])
        for child in children:
            if isinstance(child, TextDisplay):
                contents.append(child.content)
    return contents


def test_reply_view_includes_title_and_body_verbatim():
    view = reply_view("Tytuł", "Treść **markdown** bez zmian.")

    contents = _text_displays(view)
    assert any("Tytuł" in c for c in contents)
    assert any("Treść **markdown** bez zmian." in c for c in contents)


def test_reply_view_is_components_v2():
    view = reply_view("Tytuł", "Treść.")

    assert view.has_components_v2() is True


def test_reply_view_returns_layout_view():
    view = reply_view("Tytuł", "Treść.")

    assert isinstance(view, discord.ui.LayoutView)


def test_accent_for_failure_strings():
    for body in (
        replies.NOT_REGISTERED,
        replies.STATS_FAILURE,
        replies.RECENT_FAILURE,
        replies.LINK_CODES_UNCONFIGURED,
    ):
        assert accent_for(body) == Accent.FAILURE


def test_accent_for_other_text_is_brand():
    assert accent_for("Zarejestrowano w GameTrace!") == Accent.BRAND
    assert accent_for("cokolwiek innego") == Accent.BRAND
