"""Onboarding panel views (Components V2 buttons).

The panel exists because a read-only channel forbids slash commands: buttons
are the only entry path there. These tests guard the two things that break
silently in production — duplicated `custom_id`s across persistent views
(discord.py's dispatch table would overwrite one with the other) and missing
`defer()` before I/O (Discord's ~3s ack deadline).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot import panel, replies


def _interaction(*, user_id: int = 4242, username: str = "zyraf"):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, name=username),
        response=SimpleNamespace(
            defer=AsyncMock(),
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
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
    return [
        item.content
        for item in view.walk_children()
        if hasattr(item, "content") and isinstance(item.content, str)
    ]


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


def test_panel_view_renders_the_panel_title_and_body():
    """The public panel message — the one screen every visitor sees."""
    texts = _texts(panel.PanelView())
    assert f"### {replies.PANEL_TITLE}" in texts
    assert replies.panel_body() in texts


def test_member_menu_greets_a_connected_user_instead_of_repeating_the_help_text():
    """A registered user must not be told to click ▶ Zaczynam to create an
    account, which is what the help copy says."""
    texts = _texts(panel.MemberView())
    assert replies.panel_member_menu() in texts
    assert replies.panel_help_reply() not in texts


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
        ("gt:menu:code", "issue_login_code", "Kod logowania"),
        ("gt:menu:stats", "stats_command", "Statystyki"),
        ("gt:menu:recent", "recent_command", "Ostatnie sesje"),
        ("gt:menu:logout", "logout_user", "Wylogowano"),
    ],
)
async def test_menu_button_defers_then_delegates_and_renders_the_reply(
    custom_id, handler, title
):
    interaction = _interaction()
    db = AsyncMock()
    deferred_before_handler = {}

    async def fake_handler(*args, **kwargs):
        deferred_before_handler["value"] = interaction.response.defer.await_count
        return "odpowiedź"

    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)), \
         patch.object(panel, "get_redis", return_value="redis"), \
         patch.object(panel.commands, handler, new=AsyncMock(side_effect=fake_handler)) as func:
        await _callback(panel.MemberView(), custom_id)(interaction)

    func.assert_awaited_once()
    assert db in func.await_args.args
    assert "4242" in func.await_args.args
    assert deferred_before_handler["value"] == 1, "handler ran before defer()"

    view = _sent_view(interaction.followup.send)
    assert f"### {title}" in _texts(view)
    assert "odpowiedź" in _texts(view)
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


async def test_code_button_passes_the_redis_handle_and_username():
    interaction = _interaction(user_id=9, username="bob")
    db = AsyncMock()

    with patch.object(panel, "AsyncSessionLocal", _session_factory(db)), \
         patch.object(panel, "get_redis", return_value="redis-handle"), \
         patch.object(panel.commands, "issue_login_code", new=AsyncMock(return_value="kod")) as func:
        await _callback(panel.MemberView(), "gt:menu:code")(interaction)

    func.assert_awaited_once_with(db, "redis-handle", "9", "bob")


async def test_help_button_replies_without_deferring_and_without_io():
    interaction = _interaction()

    with patch.object(panel, "AsyncSessionLocal", MagicMock(side_effect=AssertionError("no I/O"))):
        await _callback(panel.PanelView(), "gt:panel:help")(interaction)

    interaction.response.defer.assert_not_awaited()
    view = _sent_view(interaction.response.send_message)
    assert replies.panel_help_reply() in _texts(view)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
