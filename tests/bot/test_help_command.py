"""Bot /help command — orientation copy, no HTTP call, no DB lookup."""
from app.bot.commands import help_command
from app.core.config import settings


def test_help_explains_presence_based_tracking(monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")

    msg = help_command()

    lowered = msg.lower()
    assert "discord" in lowered
    assert "automatycznie" in lowered


def test_help_includes_configured_web_url(monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")

    msg = help_command()

    assert "https://gametrace.example" in msg


def test_help_degrades_sensibly_without_web_url(monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "")

    msg = help_command()

    # No dangling link/wizard pointer with nowhere to point.
    assert "https://" not in msg
    assert "👉" not in msg
    assert "**Aplikacje**" not in msg


def test_help_lists_every_command(monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")

    msg = help_command()

    for command in ("/register", "/login", "/logout", "/stats", "/recent", "/help"):
        assert command in msg
