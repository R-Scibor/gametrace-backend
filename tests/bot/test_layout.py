"""V2 layout helpers — container rendering and accent selection.

No DB, no interaction handling: layout.py is pure presentation.
"""
import discord
from discord.enums import SeparatorSpacing
from discord.ui import ActionRow, Button, Container, Separator, TextDisplay

from app.bot import replies
from app.bot.layout import Accent, accent_for, card_view, reply_view


def _text_displays(view):
    """Walk the view's containers and collect TextDisplay content strings."""
    contents = []
    for item in view.children:
        children = getattr(item, "children", [item])
        for child in children:
            if isinstance(child, TextDisplay):
                contents.append(child.content)
    return contents


def _container(view):
    """The single top-level Container a reply_view produces."""
    containers = [item for item in view.children if isinstance(item, Container)]
    assert len(containers) == 1
    return containers[0]


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


def test_accent_for_panel_failure_strings():
    for body in (
        replies.PANEL_MISSING_PERMISSIONS,
        replies.PANEL_REFUSED_NOT_ADMIN,
    ):
        assert accent_for(body) == Accent.FAILURE


def test_accent_for_panel_posted_is_brand():
    assert accent_for(replies.PANEL_POSTED) == Accent.BRAND


def test_reply_view_brand_accent_reaches_container_colour():
    view = reply_view("Tytuł", "Treść.", accent=Accent.BRAND)

    assert _container(view).accent_colour == Accent.BRAND.value


def test_reply_view_failure_accent_reaches_container_colour():
    view = reply_view("Tytuł", "Treść.", accent=Accent.FAILURE)

    assert _container(view).accent_colour == Accent.FAILURE.value


def test_reply_view_brand_and_failure_produce_different_colours():
    brand_view = reply_view("Tytuł", "Treść.", accent=Accent.BRAND)
    failure_view = reply_view("Tytuł", "Treść.", accent=Accent.FAILURE)

    assert _container(brand_view).accent_colour != _container(failure_view).accent_colour


def test_accent_brand_is_gametrace_orange_not_blurple():
    """GameTrace brand orange (#ff7a1a), matching gametrace-web's theme token —
    not discord.Colour.blurple(). Guards against regressing to Discord's default."""
    assert Accent.BRAND.value == discord.Colour(0xFF7A1A)
    assert Accent.BRAND.value != discord.Colour.blurple()


def test_accent_for_output_feeds_reply_view_colour_end_to_end():
    body = replies.LINK_CODES_UNCONFIGURED

    view = reply_view("Tytuł", body, accent=accent_for(body))

    assert _container(view).accent_colour == Accent.FAILURE.value


def test_card_view_uses_soft_then_hard_gaps_and_optional_caption():
    view = card_view(title="Statystyki", caption="Ostatnie 7 dni", body="środek")

    container = _container(view)
    contents = _text_displays(view)
    assert contents[0] == "### Statystyki\n-# Ostatnie 7 dni"
    assert contents[1] == "środek"
    gaps = [child for child in container.children if isinstance(child, Separator)]
    assert gaps[0].visible is False
    assert gaps[0].spacing == SeparatorSpacing.small
    assert gaps[1].visible is True
    assert gaps[1].spacing == SeparatorSpacing.large
    assert container.accent_colour == Accent.BRAND.value


def test_card_view_omits_caption_line_when_absent():
    view = card_view(title="Statystyki", body="środek")

    assert _text_displays(view)[0] == "### Statystyki"


def _buttons(node):
    """Walk nested layout children until every Button is found."""
    found = []
    if isinstance(node, Button):
        found.append(node)
    for child in getattr(node, "children", []) or []:
        found.extend(_buttons(child))
    return found


def test_card_view_can_disable_a_button_row():
    row = ActionRow()
    row.add_item(Button(label="🔑 Weź kod", custom_id="gt:menu:code"))
    view = card_view(title="Kod logowania", body="Pobieram…", row=row, buttons_disabled=True)

    buttons = _buttons(_container(view))
    assert buttons[0].disabled is True
    assert buttons[0].custom_id == "gt:menu:code"


def test_reply_view_gap_stays_the_default_separator():
    view = reply_view("Tytuł", "Treść.")
    gaps = [child for child in _container(view).children if isinstance(child, Separator)]
    assert len(gaps) == 1
    assert gaps[0].visible is True
    assert gaps[0].spacing == SeparatorSpacing.small


def test_menu_action_failed_is_a_red_accent():
    assert accent_for(replies.MENU_ACTION_FAILED) == Accent.FAILURE


def test_english_menu_failures_are_red():
    assert accent_for(replies.LINK_CODES_UNCONFIGURED_EN) == Accent.FAILURE
    assert accent_for(replies.MENU_ACTION_FAILED_EN) == Accent.FAILURE
