"""Copy for the Components V2 onboarding panel (read-only channel entry point).

No panel string may instruct the user to type a slash command — that is the
exact dead end this feature exists to remove in a channel where Send
Messages is denied. Commands may still be *listed* (they work in DMs and
unlocked channels); only the "type this to start" instruction is forbidden.
"""
import re

import pytest

from app.bot import replies
from app.core.config import settings


def _assert_no_slash_start_instruction(text: str) -> None:
    """Catches "wpisz /login" / "wpisz /register" (Polish) and "type /login"
    / "type /register" (English) style instructions, case-insensitively,
    regardless of surrounding punctuation/backticks.

    Deliberately NOT `assert "/login" not in text` — panel_info_pl() /
    panel_info_en() legitimately list `/login` inside their "Komendy" /
    "Commands" reference sections. Instead this scans for the literal
    imperative prefixes "wpisz" (Polish "type", matching "wpisz" plus any
    word-character suffix, e.g. "wpiszcie") and "type" (English), each
    immediately followed by a slash command token. NOTE: the Polish branch
    does NOT match other conjugations such as "wpisać" (infinitive) or
    "wpisujesz" (present tense); it is scoped to the exact "wpisz ..."
    imperative shape used by the forbidden sentences ("Wpisz `/login`",
    "wpisz /login albo /register"), which is what would reappear if that
    phrasing were pasted back in. A bare command list (no "wpisz"/"type"
    nearby) is left untouched.
    """
    pattern = re.compile(
        r"(?:wpisz\w*|type)\s+(?:[`\"]?\s*)?/(?:login|register)", re.IGNORECASE
    )
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
    """Same rule as `panel_body`, for every reply rendered under a
    "GameTrace" heading: `/help` and the two panel info screens all render
    `### GameTrace` above their body. Opening the body with "**GameTrace**
    — " printed the title twice in a row."""
    assert not replies._HELP_WHAT_IS_IT.startswith("**GameTrace**")

    for web_url in ("", "https://gametrace.example"):
        monkeypatch.setattr(settings, "gametrace_web_url", web_url)
        for text in (replies.help_reply(), replies.panel_info_pl(), replies.panel_info_en()):
            assert not text.startswith("**GameTrace**")
            assert not text.startswith("GameTrace")


def test_panel_disclosure_has_no_slash_instruction():
    text = replies.panel_disclosure()
    _assert_no_slash_start_instruction(text)


def test_panel_register_confirmation_has_no_slash_instruction():
    text = replies.panel_register_confirmation()
    _assert_no_slash_start_instruction(text)


@pytest.mark.parametrize("web_url", ["", "https://gametrace.example"])
def test_panel_info_pl_has_no_slash_instruction(monkeypatch, web_url):
    monkeypatch.setattr(settings, "gametrace_web_url", web_url)
    _assert_no_slash_start_instruction(replies.panel_info_pl())


@pytest.mark.parametrize("web_url", ["", "https://gametrace.example"])
def test_panel_info_en_has_no_slash_instruction(monkeypatch, web_url):
    monkeypatch.setattr(settings, "gametrace_web_url", web_url)
    _assert_no_slash_start_instruction(replies.panel_info_en())


def test_panel_info_pl_lists_commands_as_reference():
    text = replies.panel_info_pl()
    for command in ("/login", "/register", "/logout"):
        assert command in text
    assert "Komendy" in text


def test_panel_info_en_lists_commands_as_reference():
    text = replies.panel_info_en()
    for command in ("/login", "/register", "/logout"):
        assert command in text
    assert "Commands" in text


def test_panel_info_pl_points_at_buttons():
    text = replies.panel_info_pl()
    assert "▶ START" in text
    assert "🔑 Weź kod" in text


def test_panel_info_en_points_at_buttons_using_literal_polish_labels():
    """The buttons themselves are labelled in Polish, so the English screen
    must refer to them by their exact on-screen text, not a translation."""
    text = replies.panel_info_en()
    assert "▶ START" in text
    assert "🔑 Weź kod" in text


@pytest.mark.parametrize(
    "info_fn", [replies.panel_info_pl, replies.panel_info_en], ids=["pl", "en"]
)
def test_panel_info_includes_web_links_when_configured(monkeypatch, info_fn):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    text = info_fn()
    assert "https://gametrace.example" in text
    assert "https://gametrace.example/welcome" in text
    assert "https://gametrace.example/download" in text


@pytest.mark.parametrize(
    "info_fn", [replies.panel_info_pl, replies.panel_info_en], ids=["pl", "en"]
)
def test_panel_info_has_no_dangling_link_when_web_unconfigured(monkeypatch, info_fn):
    monkeypatch.setattr(settings, "gametrace_web_url", "")
    text = info_fn()
    assert "https://" not in text
    assert "http://" not in text


@pytest.mark.parametrize(
    "info_fn", [replies.panel_info_pl, replies.panel_info_en], ids=["pl", "en"]
)
def test_panel_info_stays_within_container_text_budget(monkeypatch, info_fn):
    """Each info screen renders inside a single Components V2 container with
    a 4000-char total text budget; stay comfortably under it."""
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.rscibor.dev")
    assert len(info_fn()) < 3500


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
