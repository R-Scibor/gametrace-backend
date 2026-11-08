"""Onboarding panel views (Components V2 buttons).

The panel exists because a read-only channel forbids slash commands: buttons
are the only entry path there. These tests guard the two things that break
silently in production — duplicated `custom_id`s across persistent views
(discord.py's dispatch table would overwrite one with the other) and missing
`defer()` before I/O (Discord's ~3s ack deadline).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.enums import SeparatorSpacing
from discord.ui import Separator

from app.bot import commands, panel, replies
from app.bot.interaction_guard import reset_followups_for_tests


@pytest.fixture(autouse=True)
def _reset_followups():
    reset_followups_for_tests()
    yield
    reset_followups_for_tests()


class _TextList(list):
    """Membership is a substring check.

    `card_view` puts `### title` and `-# caption` in one TextDisplay, while
    these tests assert the title line and the caption words on their own.
    """

    def __contains__(self, item: object) -> bool:
        return any(
            item == content or (isinstance(item, str) and isinstance(content, str) and item in content)
            for content in self
        )


def _interaction(*, user_id: int = 4242, username: str = "zyraf"):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, name=username),
        message=SimpleNamespace(id=77),
        response=SimpleNamespace(
            defer=AsyncMock(),
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            is_done=MagicMock(return_value=False),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        client=SimpleNamespace(guilds=[]),
    )


def _session_factory(db):
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _callback(view, custom_id):
    for item in view.walk_children():
        if getattr(item, "custom_id", None) == custom_id:
            return item.callback
    raise AssertionError(f"{custom_id} not found on {type(view).__name__}")


def _texts(view) -> list[str]:
    return _TextList(
        item.content
        for item in view.walk_children()
        if hasattr(item, "content") and isinstance(item.content, str)
    )


def _sent_view(mock) -> object:
    mock.assert_awaited_once()
    kwargs = mock.await_args.kwargs
    # Components V2 rejects `content=` alongside a view with a 400 at runtime.
    assert "content" not in kwargs
    return kwargs["view"]


# --- 1. custom_id ownership ------------------------------------------------


def test_custom_ids_are_unique_across_every_persistent_view():
    """All persistent views share the message_id=None dispatch bucket, so a
    (component_type, custom_id) pair reused on two views means the second
    `add_view` silently steals the first one's callback."""
    seen: dict[tuple[int, str], str] = {}

    for view_cls in panel.PERSISTENT_VIEWS:
        view = view_cls()
        for item in view.walk_children():
            custom_id = getattr(item, "custom_id", None)
            if custom_id is None:
                continue
            key = (item.type.value, custom_id)
            assert key not in seen, (
                f"{custom_id} on both {seen.get(key)} and {view_cls.__name__}"
            )
            seen[key] = view_cls.__name__

    assert {cid for _, cid in seen} == {
        "gt:panel:start",
        "gt:panel:help",
        "gt:panel:help:en",
        "gt:new:accept",
        "gt:menu:code",
        "gt:menu:stats",
        "gt:menu:recent",
        "gt:menu:logout",
    }


def test_login_code_button_belongs_to_member_view_only():
    new_user_ids = {getattr(i, "custom_id", None) for i in panel.NewUserView().walk_children()}
    assert "gt:menu:code" not in new_user_ids
    assert "gt:new:accept" in new_user_ids


# --- 6. statelessness ------------------------------------------------------


@pytest.mark.parametrize("view_cls", panel.PERSISTENT_VIEWS)
def test_views_construct_with_no_arguments_and_never_time_out(view_cls):
    """`bot.add_view(cls())` at startup requires a zero-arg constructor, and a
    shared instance must not carry per-user state."""
    view = view_cls()
    assert view.timeout is None
    assert view.is_persistent()


# --- rendered copy ---------------------------------------------------------


def _custom_ids(view) -> set[str]:
    return {item.custom_id for item in view.walk_children() if getattr(item, "custom_id", None)}


def _buttons(view) -> list:
    return [item for item in view.walk_children() if getattr(item, "custom_id", None)]


def test_panel_view_renders_the_panel_title_and_body():
    """The public panel message — the one screen every visitor sees."""
    view = panel.PanelView()
    texts = _texts(view)
    assert f"### {replies.PANEL_TITLE}" in texts
    assert replies.panel_body() in texts
    gaps = [child for child in view.walk_children() if isinstance(child, Separator)]
    assert len(gaps) == 2
    assert gaps[0].visible is False
    assert gaps[0].spacing == SeparatorSpacing.small
    assert gaps[1].visible is True
    assert gaps[1].spacing == SeparatorSpacing.large
    assert view.timeout is None
    assert isinstance(view, discord.ui.LayoutView)


def test_new_user_view_stays_on_the_reply_container():
    """Disclosure stays on `_panel_container` / `reply_view`, not the card gaps."""
    view = panel.NewUserView()
    gaps = [child for child in view.walk_children() if isinstance(child, Separator)]
    assert len(gaps) == 1
    assert gaps[0].visible is True
    assert gaps[0].spacing == SeparatorSpacing.small


def test_member_menu_greets_a_connected_user_instead_of_repeating_the_help_text():
    """A registered user must not be told to click ▶ START to create an
    account, which is what the info screens say."""
    texts = _texts(panel.MemberView())
    assert replies.panel_member_menu() in texts
    assert "Konto połączone" in texts
    assert replies.panel_info_pl() not in texts
    assert replies.panel_info_en() not in texts


# --- 2. entry branch -------------------------------------------------------


async def test_start_offers_disclosure_to_an_unknown_user():
    interaction = _interaction()
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)):
        await _callback(panel.PanelView(), "gt:panel:start")(interaction)

    view = _sent_view(interaction.followup.send)
    assert isinstance(view, panel.NewUserView)
    assert replies.panel_disclosure() in _texts(view)
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


async def test_start_opens_the_member_menu_for_a_known_user():
    interaction = _interaction()
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(discord_id="4242"))

    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)):
        await _callback(panel.PanelView(), "gt:panel:start")(interaction)

    assert isinstance(_sent_view(interaction.followup.send), panel.MemberView)


async def test_start_does_not_create_an_account():
    """Account creation happens only on explicit accept."""
    interaction = _interaction()
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)), \
         patch.object(panel.commands, "register_user", new=AsyncMock()) as register:
        await _callback(panel.PanelView(), "gt:panel:start")(interaction)

    register.assert_not_awaited()
    db.add.assert_not_called()


# --- 3. accept path --------------------------------------------------------


async def test_accept_registers_then_edits_the_message_into_the_member_menu():
    interaction = _interaction(user_id=77, username="ala")
    db = AsyncMock()

    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)), \
         patch.object(panel.commands, "register_user", new=AsyncMock(return_value="ok")) as register:
        await _callback(panel.NewUserView(), "gt:new:accept")(interaction)

    register.assert_awaited_once_with(db, "77", "ala")
    interaction.followup.send.assert_not_awaited()
    interaction.response.send_message.assert_not_awaited()

    view = _sent_view(interaction.response.edit_message)
    assert isinstance(view, panel.MemberView)
    assert replies.panel_register_confirmation() in _texts(view)


async def test_accept_does_not_defer_because_edit_message_is_the_response():
    interaction = _interaction()
    db = AsyncMock()

    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)), \
         patch.object(panel.commands, "register_user", new=AsyncMock(return_value="ok")):
        await _callback(panel.NewUserView(), "gt:new:accept")(interaction)

    interaction.response.defer.assert_not_awaited()


# --- 4. delegation + 5. deferral ------------------------------------------


@pytest.mark.parametrize(
    "custom_id,handler,title",
    [
        ("gt:menu:code", "issue_login_code_facts", "Kod logowania"),
        ("gt:menu:stats", "stats_facts", "Statystyki"),
        ("gt:menu:recent", "recent_facts", "Ostatnie sesje"),
        ("gt:menu:logout", "logout_user_facts", "Wylogowano"),
    ],
)
async def test_menu_button_edits_loading_then_the_result(custom_id, handler, title):
    interaction = _interaction()
    db = AsyncMock()
    edited_before_handler = {}

    async def fake_handler(*args, **kwargs):
        edited_before_handler["value"] = interaction.response.edit_message.await_count
        if handler == "issue_login_code_facts":
            return commands.LoginCodeFacts(unconfigured=False, created=False, code="482 913")
        if handler == "logout_user_facts":
            return commands.LogoutFacts(unregistered=False, count=2)
        if handler == "stats_facts":
            return commands.StatsFacts(
                slash_body="ok",
                total_seconds=3600,
                per_game=[{"game_name": "Hades II", "total_seconds": 3600}],
            )
        return commands.RecentFacts(slash_body="ok", sessions=[], user_timezone="UTC")

    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)), \
         patch.object(panel, "get_redis", return_value="redis"), \
         patch.object(panel.commands, handler, new=AsyncMock(side_effect=fake_handler)):
        await _callback(panel.MemberView(), custom_id)(interaction)

    assert edited_before_handler["value"] == 1
    loading = _sent_view(interaction.response.edit_message)
    assert "Pobieram…" in _texts(loading)
    assert f"### {title}" in _texts(loading)
    loading_buttons = [
        item for item in loading.walk_children() if getattr(item, "custom_id", None) == custom_id
    ]
    assert loading_buttons[0].disabled is True
    interaction.followup.send.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    result = interaction.edit_original_response.await_args.kwargs["view"]
    result_texts = _texts(result)
    assert f"### {title}" in result_texts
    assert "Pobieram…" not in result_texts
    if handler == "issue_login_code_facts":
        assert "482 913" in result_texts
    elif handler == "stats_facts":
        assert "Hades II" in result_texts
    elif handler == "recent_facts":
        assert replies.recent_empty() in result_texts
    else:
        assert "-# 2 tokenów" in result_texts
    result_buttons = [
        item for item in result.walk_children() if getattr(item, "custom_id", None) == custom_id
    ]
    assert result_buttons[0].disabled is False


async def test_menu_failure_reenables_after_the_handler_raises():
    interaction = _interaction()
    db = AsyncMock()
    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)), \
         patch.object(panel, "get_redis", return_value="redis"), \
         patch.object(panel.commands, "stats_facts", new=AsyncMock(side_effect=RuntimeError("db"))):
        await _callback(panel.MemberView(), "gt:menu:stats")(interaction)

    failure = interaction.edit_original_response.await_args.kwargs["view"]
    assert replies.MENU_ACTION_FAILED in _texts(failure)
    buttons = [item for item in failure.walk_children() if getattr(item, "custom_id", None) == "gt:menu:stats"]
    assert buttons[0].disabled is False


async def test_menu_result_does_not_use_followup_send():
    interaction = _interaction()
    db = AsyncMock()
    db.get = AsyncMock(return_value=object())
    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)), \
         patch.object(panel, "get_redis", return_value="redis"), \
         patch.object(panel.commands, "issue_login_code_facts", new=AsyncMock(
             return_value=commands.LoginCodeFacts(False, False, "111 222")
         )):
        await _callback(panel.MemberView(), "gt:menu:code")(interaction)
    interaction.followup.send.assert_not_awaited()
    assert panel.PanelView().timeout is None
    assert _callback(panel.PanelView(), "gt:panel:start")


async def test_code_button_passes_the_redis_handle_and_username():
    interaction = _interaction(user_id=9, username="bob")
    db = AsyncMock()

    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)), \
         patch.object(panel, "get_redis", return_value="redis-handle"), \
         patch.object(panel.commands, "issue_login_code_facts", new=AsyncMock(
             return_value=commands.LoginCodeFacts(False, False, "111 222")
         )) as func:
        await _callback(panel.MemberView(), "gt:menu:code")(interaction)

    func.assert_awaited_once_with(db, "redis-handle", "9", "bob")


async def test_configured_code_refreshes_membership():
    interaction = _interaction(user_id=9, username="bob")
    db = AsyncMock()
    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)), \
         patch.object(panel, "get_redis", return_value="redis-handle"), \
         patch.object(panel.commands, "issue_login_code_facts", new=AsyncMock(
             return_value=commands.LoginCodeFacts(unconfigured=False, created=False, code="482 913")
         )), \
         patch.object(panel.membership, "refresh_user", new=AsyncMock()) as refresh:
        await _callback(panel.MemberView(), "gt:menu:code")(interaction)

    refresh.assert_awaited_once_with("redis-handle", interaction.client, "9")


async def test_unconfigured_code_does_not_refresh_membership():
    interaction = _interaction()
    db = AsyncMock()
    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)), \
         patch.object(panel, "get_redis", return_value="redis"), \
         patch.object(panel.commands, "issue_login_code_facts", new=AsyncMock(
             return_value=commands.LoginCodeFacts(unconfigured=True, created=False, code="")
         )), \
         patch.object(panel.membership, "refresh_user", new=AsyncMock()) as refresh:
        await _callback(panel.MemberView(), "gt:menu:code")(interaction)

    refresh.assert_not_awaited()
    result = interaction.edit_original_response.await_args.kwargs["view"]
    assert replies.LINK_CODES_UNCONFIGURED in _texts(result)


# --- 7. persistent-view registration hygiene -------------------------------


_VIEW_CARRYING_FOLLOWUP_BUTTONS = [
    ("gt:panel:start", "PanelView"),
]


@pytest.mark.parametrize("custom_id,view_name", _VIEW_CARRYING_FOLLOWUP_BUTTONS)
async def test_ephemeral_followups_carrying_a_view_ask_for_the_message_back(
    custom_id, view_name
):
    """`wait=True` is what gives the sent view a message ID to be filed under.

    An ephemeral followup that carries a dispatchable view gets
    `view.timeout = 15 * 60` forced on it by discord.py. If that instance was
    stored without a message ID it would sit in `ViewStore._views[None]` —
    the same bucket `bot.add_view()` fills at startup — and its expiry would
    pop the shared `(component_type, custom_id)` keys, deregistering the
    panel buttons for everyone.
    """
    interaction = _interaction()
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)):
        await _callback(getattr(panel, view_name)(), custom_id)(interaction)

    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs["view"] is not None
    assert kwargs.get("wait") is True, f"{custom_id} followup must request the message ID"


@pytest.mark.parametrize(
    "custom_id,view_name",
    [
        ("gt:panel:start", "PanelView"),
    ],
)
async def test_button_defers_are_thinking_defers(custom_id, view_name):
    """These are *component* interactions: discord.py maps a bare
    `defer(ephemeral=True)` to DEFERRED_MESSAGE_UPDATE and drops `ephemeral`
    entirely, so the click shows no loading state at all. Only
    `thinking=True` produces the "Bot is thinking…" placeholder — without it
    a slow lookup looks like a dead button and gets clicked twice, which on
    `gt:menu:code` mints and burns a second login code."""
    interaction = _interaction()
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)):
        await _callback(getattr(panel, view_name)(), custom_id)(interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)


async def test_help_button_renders_the_polish_info_screen_without_deferring_or_io():
    """`gt:panel:help` is deliberately kept for the Polish screen — panels
    already posted to the live server carry a button with this exact
    custom_id, so renaming it would leave those buttons dispatching
    nowhere. It opens the what-card, not the whole info wall."""
    interaction = _interaction()

    with patch.object(panel, "AsyncSessionLocal", MagicMock(side_effect=AssertionError("no I/O"))):
        await _callback(panel.PanelView(), "gt:panel:help")(interaction)

    interaction.response.defer.assert_not_awaited()
    view = _sent_view(interaction.response.send_message)
    texts = "\n".join(_texts(view))
    assert "GameTrace to tracker" in texts
    assert "/login" not in texts
    assert replies.panel_info_pl() not in texts
    assert "### Co to jest?" in texts
    ids = _custom_ids(view)
    assert "gt:info:pl:start" in ids or "gt:info:pl:start:noweb" in ids
    assert "gt:panel:help" not in ids
    assert "gt:menu:code" not in ids
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_help_en_button_renders_the_english_info_screen_without_deferring_or_io():
    interaction = _interaction()

    with patch.object(panel, "AsyncSessionLocal", MagicMock(side_effect=AssertionError("no I/O"))):
        await _callback(panel.PanelView(), "gt:panel:help:en")(interaction)

    interaction.response.defer.assert_not_awaited()
    view = _sent_view(interaction.response.send_message)
    texts = "\n".join(_texts(view))
    assert "GameTrace is a playtime tracker" in texts
    assert "/login" not in texts
    assert replies.panel_info_en() not in texts
    assert "### What is it?" in texts
    ids = _custom_ids(view)
    assert "gt:info:en:start" in ids or "gt:info:en:start:noweb" in ids
    assert "gt:panel:help" not in ids
    assert "gt:panel:help:en" not in ids
    assert "gt:menu:code" not in ids
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_polish_info_opens_the_what_card_not_the_whole_wall():
    interaction = _interaction()
    with patch.object(panel, "AsyncSessionLocal", MagicMock(side_effect=AssertionError("no I/O"))):
        await _callback(panel.PanelView(), "gt:panel:help")(interaction)
    view = _sent_view(interaction.response.send_message)
    texts = "\n".join(_texts(view))
    assert "GameTrace to tracker" in texts
    assert "/login" not in texts
    ids = _custom_ids(view)
    assert "gt:info:pl:start" in ids or "gt:info:pl:start:noweb" in ids
    assert "gt:panel:help" not in ids
    assert "gt:menu:code" not in ids
    assert all(":what" not in custom_id for custom_id in ids)
    start = next(button for button in _buttons(view) if button.custom_id.startswith("gt:info:pl:start"))
    assert start.label == "Jak zacząć"
    assert start.style == discord.ButtonStyle.primary


async def test_english_info_opens_the_what_card_not_the_whole_wall():
    interaction = _interaction()
    with patch.object(panel, "AsyncSessionLocal", MagicMock(side_effect=AssertionError("no I/O"))):
        await _callback(panel.PanelView(), "gt:panel:help:en")(interaction)
    view = _sent_view(interaction.response.send_message)
    texts = "\n".join(_texts(view))
    assert "GameTrace is a playtime tracker" in texts
    assert "/login" not in texts
    ids = _custom_ids(view)
    assert "gt:info:en:start" in ids or "gt:info:en:start:noweb" in ids
    assert "gt:panel:help:en" not in ids
    assert "gt:menu:code" not in ids
    assert all(":what" not in custom_id for custom_id in ids)


async def test_start_subbutton_edits_in_place_and_does_not_follow_up(monkeypatch):
    monkeypatch.setattr("app.bot.replies.settings.gametrace_web_url", "https://gt.example")
    opener = _interaction()
    with patch.object(panel, "AsyncSessionLocal", MagicMock(side_effect=AssertionError("no I/O"))):
        await _callback(panel.PanelView(), "gt:panel:help")(opener)
    opened = _sent_view(opener.response.send_message)
    click = _interaction()
    await _callback(opened, "gt:info:pl:start")(click)
    click.response.edit_message.assert_awaited()
    click.followup.send.assert_not_awaited()
    edited = "\n".join(_texts(click.response.edit_message.await_args.kwargs["view"]))
    assert "Weź kod" in edited
    assert "/login" not in edited


async def test_info_without_a_web_url_uses_noweb_ids_and_omits_the_app_button(monkeypatch):
    monkeypatch.setattr("app.bot.replies.settings.gametrace_web_url", "")
    interaction = _interaction()
    with patch.object(panel, "AsyncSessionLocal", MagicMock(side_effect=AssertionError("no I/O"))):
        await _callback(panel.PanelView(), "gt:panel:help")(interaction)
    view = _sent_view(interaction.response.send_message)
    ids = _custom_ids(view)
    assert "gt:info:pl:start:noweb" in ids
    assert "gt:info:pl:commands:noweb" in ids
    assert "gt:info:pl:app:noweb" not in ids
    assert "gt:info:pl:what:noweb" not in ids
    labels = {item.label for item in _buttons(view)}
    assert "Aplikacja" not in labels
    assert "Jak zacząć" in labels
    assert "Komendy" in labels

    english = _interaction()
    with patch.object(panel, "AsyncSessionLocal", MagicMock(side_effect=AssertionError("no I/O"))):
        await _callback(panel.PanelView(), "gt:panel:help:en")(english)
    en_view = _sent_view(english.response.send_message)
    en_ids = _custom_ids(en_view)
    assert "gt:info:en:start:noweb" in en_ids
    assert "gt:info:en:commands:noweb" in en_ids
    assert "gt:info:en:app:noweb" not in en_ids
    assert "App" not in {item.label for item in _buttons(en_view)}


async def test_other_info_screens_put_what_on_the_left_and_omit_themselves(monkeypatch):
    monkeypatch.setattr("app.bot.replies.settings.gametrace_web_url", "https://gt.example")
    opener = _interaction()
    with patch.object(panel, "AsyncSessionLocal", MagicMock(side_effect=AssertionError("no I/O"))):
        await _callback(panel.PanelView(), "gt:panel:help")(opener)
    opened = _sent_view(opener.response.send_message)
    click = _interaction()
    await _callback(opened, "gt:info:pl:commands")(click)
    view = click.response.edit_message.await_args.kwargs["view"]
    buttons = _buttons(view)
    assert [button.custom_id for button in buttons] == [
        "gt:info:pl:what",
        "gt:info:pl:start",
        "gt:info:pl:app",
    ]
    assert buttons[0].label == "Co to jest?"
    click.followup.send.assert_not_awaited()
    click.response.defer.assert_not_awaited()


async def test_missing_ephemeral_id_stops_the_info_view():
    interaction = _interaction()
    interaction.response.send_message = AsyncMock(return_value=SimpleNamespace(message_id=None))
    with patch.object(panel, "AsyncSessionLocal", MagicMock(side_effect=AssertionError("no I/O"))):
        await _callback(panel.PanelView(), "gt:panel:help")(interaction)
    view = _sent_view(interaction.response.send_message)
    assert view.is_finished()
    assert view.timeout == 15 * 60


async def test_returned_message_id_keeps_the_view_and_the_next_click_stops_it(monkeypatch):
    monkeypatch.setattr("app.bot.replies.settings.gametrace_web_url", "https://gt.example")
    panel._info_views.clear()
    opener = _interaction()
    opener.response.send_message = AsyncMock(return_value=SimpleNamespace(message_id=555))
    with patch.object(panel, "AsyncSessionLocal", MagicMock(side_effect=AssertionError("no I/O"))):
        await _callback(panel.PanelView(), "gt:panel:help")(opener)
    opened = _sent_view(opener.response.send_message)
    assert opened.is_finished() is False
    assert panel._info_views[555] is opened
    click = _interaction()
    click.message = SimpleNamespace(id=555)
    await _callback(opened, "gt:info:pl:start")(click)
    assert opened.is_finished() is True
    click.response.edit_message.assert_awaited()
    click.followup.send.assert_not_awaited()
    edited = click.response.edit_message.await_args.kwargs["view"]
    assert edited is not opened
    assert panel._info_views[555] is edited
    assert edited.timeout == 15 * 60
    assert "InfoCardView" not in {cls.__name__ for cls in panel.PERSISTENT_VIEWS}
