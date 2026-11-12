"""Slash commands and panel buttons consult the interaction guard.

Guard names are patched on the module that calls them (`app.bot.main` or
`app.bot.panel`). Patching `app.bot.interaction_guard` alone misses the
already-imported bindings.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot import commands, panel
from app.bot import main as bot_main
from app.bot.interaction_guard import release_followup as real_release_followup
from app.bot.interaction_guard import reset_followups_for_tests, try_acquire_followup
from tests.bot.test_panel import _callback, _interaction, _session_factory, _texts


@pytest.fixture(autouse=True)
def _reset_followups():
    reset_followups_for_tests()
    panel._menu_inflight.discard(77)
    yield
    reset_followups_for_tests()
    panel._menu_inflight.discard(77)


def _buttons(view) -> list:
    return [item for item in view.walk_children() if getattr(item, "custom_id", None)]


async def test_slash_login_cooldown_refuses_without_issuing_a_code():
    interaction = _interaction()
    issue = AsyncMock(return_value="kod 111 222")
    facts = AsyncMock()
    claim = AsyncMock()
    acquire = MagicMock(return_value=True)

    with patch.object(bot_main, "cooldown_remaining", new=AsyncMock(return_value=12), create=True), \
         patch.object(bot_main, "claim_cooldown", claim, create=True), \
         patch.object(bot_main, "try_acquire_followup", acquire, create=True), \
         patch.object(bot_main, "get_redis", return_value="redis"), \
         patch.object(bot_main, "AsyncSessionLocal", _session_factory(AsyncMock())), \
         patch.object(bot_main.membership, "refresh_user", new=AsyncMock()), \
         patch("app.bot.commands.issue_login_code", issue), \
         patch("app.bot.commands.issue_login_code_facts", facts):
        await bot_main.login_command.callback(interaction)

    view = interaction.response.send_message.await_args.kwargs["view"]
    assert "Poczekaj jeszcze 12 s" in _texts(view)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    issue.assert_not_awaited()
    facts.assert_not_awaited()
    claim.assert_not_awaited()
    acquire.assert_not_called()


async def test_slash_stats_busy_sends_without_defer_or_claim():
    interaction = _interaction()
    claim = AsyncMock()

    with patch.object(bot_main, "cooldown_remaining", new=AsyncMock(return_value=None), create=True), \
         patch.object(bot_main, "try_acquire_followup", return_value=False, create=True), \
         patch.object(bot_main, "claim_cooldown", claim, create=True), \
         patch.object(bot_main, "get_redis", return_value="redis"), \
         patch.object(bot_main, "AsyncSessionLocal", _session_factory(AsyncMock())), \
         patch("app.bot.commands.stats_command", new=AsyncMock(return_value="ok")):
        await bot_main.stats_command_handler.callback(interaction)

    interaction.response.defer.assert_not_awaited()
    claim.assert_not_awaited()
    view = interaction.response.send_message.await_args.kwargs["view"]
    assert "Za dużo próśb naraz" in _texts(view)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_menu_code_cooldown_edits_an_enabled_card_when_idle():
    interaction = _interaction()
    panel._menu_inflight.discard(77)
    facts = AsyncMock(return_value=commands.LoginCodeFacts(False, False, "111 222"))
    claim = AsyncMock()
    acquire = MagicMock(return_value=True)

    with patch.object(panel, "cooldown_remaining", new=AsyncMock(return_value=12), create=True), \
         patch.object(panel, "claim_cooldown", claim, create=True), \
         patch.object(panel, "try_acquire_followup", acquire, create=True), \
         patch.object(panel, "get_redis", return_value="redis"), \
         patch.object(panel, "AsyncSessionLocal", _session_factory(AsyncMock())), \
         patch.object(panel.commands, "issue_login_code_facts", facts), \
         patch.object(panel.membership, "refresh_user", new=AsyncMock()):
        await _callback(panel.MemberView(), "gt:menu:code")(interaction)

    facts.assert_not_awaited()
    claim.assert_not_awaited()
    acquire.assert_not_called()
    view = interaction.response.edit_message.await_args.kwargs["view"]
    assert "Wait 12 s before asking for a new code." in _texts(view)
    assert "Pobieram…" not in _texts(view)
    buttons = _buttons(view)
    assert any(button.custom_id == "gt:menu:code" for button in buttons)
    assert all(button.disabled is False for button in buttons)
    interaction.response.defer.assert_not_awaited()


async def test_menu_code_cooldown_defers_when_the_card_is_inflight():
    interaction = _interaction()
    panel._menu_inflight.add(77)
    facts = AsyncMock(return_value=commands.LoginCodeFacts(False, False, "111 222"))

    try:
        with patch.object(panel, "cooldown_remaining", new=AsyncMock(return_value=12), create=True), \
             patch.object(panel, "get_redis", return_value="redis"), \
             patch.object(panel, "AsyncSessionLocal", _session_factory(AsyncMock())), \
             patch.object(panel.commands, "issue_login_code_facts", facts), \
             patch.object(panel.membership, "refresh_user", new=AsyncMock()):
            await _callback(panel.MemberView(), "gt:menu:code")(interaction)
    finally:
        panel._menu_inflight.discard(77)

    interaction.response.defer.assert_awaited_once_with()
    interaction.response.edit_message.assert_not_awaited()
    facts.assert_not_awaited()


async def test_menu_stats_allowed_releases_the_followup_slot():
    interaction = _interaction()
    reset_followups_for_tests()
    try:
        with patch.object(panel, "cooldown_remaining", new=AsyncMock(return_value=None), create=True), \
             patch.object(panel, "claim_cooldown", new=AsyncMock(return_value=None), create=True), \
             patch.object(panel, "get_redis", return_value="redis"), \
             patch.object(panel, "AsyncSessionLocal", _session_factory(AsyncMock())), \
             patch.object(
                 panel.commands,
                 "stats_facts",
                 new=AsyncMock(
                     return_value=commands.StatsFacts(
                         slash_body="ok",
                         total_seconds=3600,
                         per_game=[{"game_name": "Hades II", "total_seconds": 3600}],
                     )
                 ),
             ):
            await _callback(panel.MemberView(), "gt:menu:stats")(interaction)

        loading = interaction.response.edit_message.await_args.kwargs["view"]
        assert "Loading…" in _texts(loading)
        interaction.edit_original_response.assert_awaited()
        assert all(try_acquire_followup() for _ in range(8)) is True
    finally:
        reset_followups_for_tests()


async def test_lost_claim_releases_the_followup_slot_once():
    interaction = _interaction()
    reset_followups_for_tests()
    wrapped_release = MagicMock(wraps=real_release_followup)
    facts = AsyncMock(
        return_value=commands.StatsFacts(slash_body="ok", total_seconds=0, per_game=[])
    )
    try:
        with patch.object(panel, "release_followup", wrapped_release, create=True), \
             patch.object(panel, "cooldown_remaining", new=AsyncMock(return_value=None), create=True), \
             patch.object(panel, "claim_cooldown", new=AsyncMock(return_value=4), create=True), \
             patch.object(panel, "get_redis", return_value="redis"), \
             patch.object(panel, "AsyncSessionLocal", _session_factory(AsyncMock())), \
             patch.object(panel.commands, "stats_facts", facts):
            await _callback(panel.MemberView(), "gt:menu:stats")(interaction)

        assert wrapped_release.call_count == 1
        view = interaction.response.edit_message.await_args.kwargs["view"]
        assert "Wait 4 s before using this again." in _texts(view)
        facts.assert_not_awaited()
        assert all(try_acquire_followup() for _ in range(8)) is True
    finally:
        reset_followups_for_tests()


async def test_code_button_is_inflight_before_the_loading_edit_returns():
    interaction = _interaction()
    inflight_during_edit = {}

    async def edit_message(*args, **kwargs):
        inflight_during_edit["value"] = interaction.message.id in panel._menu_inflight

    interaction.response.edit_message = AsyncMock(side_effect=edit_message)

    with patch.object(panel, "cooldown_remaining", new=AsyncMock(return_value=None), create=True), \
         patch.object(panel, "claim_cooldown", new=AsyncMock(return_value=None), create=True), \
         patch.object(panel, "get_redis", return_value="redis"), \
         patch.object(panel, "AsyncSessionLocal", _session_factory(AsyncMock())), \
         patch.object(
             panel.commands,
             "issue_login_code_facts",
             new=AsyncMock(return_value=commands.LoginCodeFacts(False, False, "111 222")),
         ), \
         patch.object(panel.membership, "refresh_user", new=AsyncMock()):
        await _callback(panel.MemberView(), "gt:menu:code")(interaction)

    assert inflight_during_edit["value"] is True
    assert interaction.message.id not in panel._menu_inflight


async def test_stats_defers_without_a_slot_when_the_card_is_already_inflight():
    interaction = _interaction()
    panel._menu_inflight.add(interaction.message.id)
    facts = AsyncMock(return_value=commands.StatsFacts(slash_body="ok", total_seconds=0, per_game=[]))
    claim = AsyncMock(return_value=None)
    reset_followups_for_tests()
    try:
        with patch.object(panel, "cooldown_remaining", new=AsyncMock(return_value=None), create=True), \
             patch.object(panel, "claim_cooldown", claim, create=True), \
             patch.object(panel, "get_redis", return_value="redis"), \
             patch.object(panel, "AsyncSessionLocal", _session_factory(AsyncMock())), \
             patch.object(panel.commands, "stats_facts", facts):
            await _callback(panel.MemberView(), "gt:menu:stats")(interaction)

        interaction.response.defer.assert_awaited_once_with()
        interaction.response.edit_message.assert_not_awaited()
        claim.assert_not_awaited()
        facts.assert_not_awaited()
        assert interaction.message.id in panel._menu_inflight
        assert all(try_acquire_followup() for _ in range(8)) is True
    finally:
        panel._menu_inflight.discard(interaction.message.id)
        reset_followups_for_tests()


async def test_start_busy_sends_an_ephemeral_notice_without_the_menu():
    interaction = _interaction()
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with patch.object(panel, "try_acquire_followup", return_value=False, create=True), \
         patch.object(panel, "AsyncSessionLocal", _session_factory(db)):
        await _callback(panel.PanelView(), "gt:panel:start")(interaction)

    sent = interaction.response.send_message.await_args
    assert sent is not None
    assert sent.kwargs["ephemeral"] is True
    view = sent.kwargs["view"]
    assert "GameTrace" in _texts(view)
    assert "Za dużo próśb naraz. Spróbuj ponownie za chwilę." in _texts(view)
    interaction.response.defer.assert_not_awaited()
    interaction.response.edit_message.assert_not_awaited()
    assert all(getattr(child, "custom_id", None) != "gt:menu:code" for child in view.walk_children())


async def test_menu_followup_cap_uses_the_saved_language():
    cases = (
        ("en", "Too many requests at once. Try again in a moment.", "Za dużo próśb naraz"),
        ("pl", "Za dużo próśb naraz. Spróbuj ponownie za chwilę.", None),
    )
    for language, expected, forbidden in cases:
        interaction = _interaction()
        panel._menu_inflight.discard(77)
        db = AsyncMock()
        db.get = AsyncMock(return_value=SimpleNamespace(language=language))
        facts = AsyncMock(return_value=commands.LoginCodeFacts(False, False, "111 222"))

        with patch.object(panel, "cooldown_remaining", new=AsyncMock(return_value=None), create=True), \
             patch.object(panel, "try_acquire_followup", return_value=False, create=True), \
             patch.object(panel, "get_redis", return_value="redis"), \
             patch.object(panel, "AsyncSessionLocal", _session_factory(db)), \
             patch.object(panel.commands, "issue_login_code_facts", facts), \
             patch.object(panel.membership, "refresh_user", new=AsyncMock()):
            await _callback(panel.MemberView(), "gt:menu:code")(interaction)

        facts.assert_not_awaited()
        interaction.response.defer.assert_not_awaited()
        view = interaction.response.edit_message.await_args.kwargs["view"]
        texts = _texts(view)
        assert expected in texts
        if forbidden is not None:
            assert forbidden not in texts
