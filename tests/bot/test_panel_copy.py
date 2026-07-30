"""Copy for the Components V2 onboarding panel (read-only channel entry point).

No panel string may instruct the user to type a slash command — that is the
exact dead end this feature exists to remove in a channel where Send
Messages is denied. Commands may still be *listed* (they work in DMs and
unlocked channels); only the "type this to start" instruction is forbidden.
"""
import re

from app.bot import replies
from app.core.config import settings


def _assert_no_slash_start_instruction(text: str) -> None:
    """Catches "wpisz /login" / "wpisz /register" style instructions,
    case-insensitively, regardless of surrounding punctuation/backticks.

    Deliberately NOT `assert "/login" not in text` — panel_help_reply()
    legitimately lists `/login` inside the composed _HELP_COMMANDS block.
    Instead this scans for the literal prefix "wpisz" (Polish imperative
    "type") — matching "wpisz" itself plus any word-character suffix, e.g.
    "wpiszcie" — immediately followed by a slash command token. NOTE: this
    does NOT match other conjugations such as "wpisać" (infinitive) or
    "wpisujesz" (present tense); it is scoped to the exact "wpisz ..."
    imperative shape used by both forbidden sentences ("Wpisz `/login`",
    "wpisz /login albo /register"), which is what would reappear if that
    phrasing were pasted back in. A bare command list (no "wpisz" nearby)
    is left untouched.
    """
    pattern = re.compile(r"wpisz\w*\s+(?:[`\"]?\s*)?/(?:login|register)", re.IGNORECASE)
    match = pattern.search(text)
    assert not match, f"panel copy instructs typing a slash command: {match.group(0)!r}"


def test_panel_title_is_a_nonempty_string():
    assert isinstance(replies.PANEL_TITLE, str)
    assert replies.PANEL_TITLE.strip()


def test_panel_body_has_no_slash_instruction():
    text = replies.panel_body()
    _assert_no_slash_start_instruction(text)


def test_panel_body_does_not_restate_title():
    """reply_view() (a later task) renders PANEL_TITLE as a heading above
    the body; the body must not open with it again or the panel shows the
    title twice in a row."""
    text = replies.panel_body()
    assert not text.startswith(replies.PANEL_TITLE)


def test_help_copy_does_not_restate_the_brand_title(monkeypatch):
    """Same rule as `panel_body`, for the other constant rendered under a
    "GameTrace" heading: `_HELP_WHAT_IS_IT` feeds both `/help` and the
    panel's help screen, and both render `### GameTrace` above it. Opening
    the body with "**GameTrace** — " printed the title twice in a row."""
    assert not replies._HELP_WHAT_IS_IT.startswith("**GameTrace**")

    for web_url in ("", "https://gametrace.example"):
        monkeypatch.setattr(settings, "gametrace_web_url", web_url)
        for text in (replies.help_reply(), replies.panel_help_reply()):
            assert not text.startswith("**GameTrace**")
            assert not text.startswith("GameTrace")


def test_panel_disclosure_has_no_slash_instruction():
    text = replies.panel_disclosure()
    _assert_no_slash_start_instruction(text)


def test_panel_register_confirmation_has_no_slash_instruction():
    text = replies.panel_register_confirmation()
    _assert_no_slash_start_instruction(text)


def test_panel_help_reply_has_no_slash_instruction():
    text = replies.panel_help_reply()
    _assert_no_slash_start_instruction(text)


def test_panel_help_reply_still_lists_commands():
    text = replies.panel_help_reply()
    for command in ("/register", "/login", "/logout", "/stats", "/recent", "/help"):
        assert command in text


def test_panel_help_reply_composes_existing_constants():
    text = replies.panel_help_reply()
    assert replies._HELP_WHAT_IS_IT in text
    assert replies._HELP_COMMANDS in text
    # The old instructional block must not be reused verbatim on the panel.
    assert replies._HELP_HOW_TO_START_NO_APP not in text


def test_panel_help_reply_points_at_button():
    text = replies.panel_help_reply()
    assert "▶ Zaczynam" in text or "Zaczynam" in text


def test_panel_disclosure_names_recorded_fields():
    text = replies.panel_disclosure().lower()
    assert "gr" in text  # game name mentioned
    assert "rozpocz" in text or "start" in text  # session start time
    assert "koniec" in text or "zakończ" in text or "end" in text  # session end time


def test_panel_disclosure_includes_privacy_link_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "gametrace_privacy_url", "https://gametrace.example/privacy")

    text = replies.panel_disclosure()

    assert "https://gametrace.example/privacy" in text


def test_panel_disclosure_has_no_dangling_link_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "gametrace_privacy_url", "")

    text = replies.panel_disclosure()

    assert "https://" not in text


def test_panel_register_confirmation_points_at_code_button():
    text = replies.panel_register_confirmation()
    assert "Weź kod" in text


def test_settings_has_privacy_url_default_empty():
    assert settings.gametrace_privacy_url == ""
