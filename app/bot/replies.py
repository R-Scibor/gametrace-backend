"""Polish user-facing copy for Discord bot slash commands.

Kept separate from command handlers (app/bot/commands.py) so tone and
wording can be reviewed in one place, independent of the API-calling logic
around them. Every string here ends up as an ephemeral Discord reply.
"""
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings

NOT_REGISTERED = "Nie jesteś zarejestrowany. Użyj `/register` lub `/login`, aby zacząć."

STATS_FAILURE = "Nie udało się pobrać statystyk. Spróbuj ponownie za chwilę."

RECENT_FAILURE = "Nie udało się pobrać ostatnich sesji. Spróbuj ponownie za chwilę."


def _web_hint() -> str:
    return f" {settings.gametrace_web_url}" if settings.gametrace_web_url else ""


def _format_duration(total_seconds: int) -> str:
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours and minutes:
        return f"{hours} godz. {minutes} min"
    if hours:
        return f"{hours} godz."
    return f"{minutes} min"


def stats_empty() -> str:
    """First-run / quiet-week state. Must read as a promise, not a dead end."""
    return (
        "Jeszcze nie mam żadnych sesji z ostatnich 7 dni, ale już Cię obserwuję — "
        "zagraj w cokolwiek, a Twój czas zacznie się tu pojawiać. "
        f"Wróć po sesji i sprawdź `/stats` ponownie.{_web_hint()}"
    )


def stats_reply(
    *,
    total_seconds: int,
    per_game: list[dict],
    pending_errors_count: int,
    review_count: int,
) -> str:
    if total_seconds <= 0 or not per_game:
        return stats_empty()

    lines = [f"Ostatnie 7 dni: **{_format_duration(total_seconds)}** łącznie."]

    top_games = per_game[:3]
    if top_games:
        lines.append("")
        lines.append("Najwięcej grane:")
        for entry in top_games:
            name = entry.get("game_name", "?")
            secs = entry.get("total_seconds", 0)
            lines.append(f"- {name} — {_format_duration(secs)}")

    if pending_errors_count > 0:
        lines.append("")
        lines.append(
            f"Masz {pending_errors_count} sesje wymagające poprawy — "
            f"popraw je w aplikacji.{_web_hint()}"
        )

    if review_count > 0:
        lines.append("")
        lines.append(
            f"{review_count} gier czeka na potwierdzenie w aplikacji.{_web_hint()}"
        )

    return "\n".join(lines)


def _resolve_tz(tz_name: str | None) -> ZoneInfo:
    """Literal per-user timezone. Falls back to UTC on missing/invalid data —
    unlike the voice pipeline's resolver, `/recent` has no reason to treat a
    literal "UTC" as "unset"."""
    try:
        return ZoneInfo(tz_name) if tz_name else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_session_line(entry: dict, tz: ZoneInfo) -> str:
    game_name = (entry.get("game") or {}).get("primary_name", "?")
    start = _parse_iso(entry.get("start_time"))
    time_str = start.astimezone(tz).strftime("%d.%m %H:%M") if start else "?"

    duration_seconds = entry.get("duration_seconds")
    if duration_seconds is not None:
        detail = _format_duration(duration_seconds)
    elif entry.get("status") == "ERROR":
        detail = "błąd, brak czasu trwania"
    else:
        detail = "brak czasu trwania"

    return f"- **{game_name}** — {time_str} ({detail})"


def recent_empty() -> str:
    """First-run / quiet-history state. Same promise-not-dead-end bar as /stats."""
    return (
        "Jeszcze nie mam żadnych zarejestrowanych sesji, ale już Cię obserwuję — "
        f"zagraj w cokolwiek, a pojawi się tutaj po zakończeniu.{_web_hint()}"
    )


def help_reply() -> str:
    """Orientation for someone who noticed the bot and has no idea what it is.

    Discord's own slash-command picker already lists every command with its
    description, so this is prose about *why the bot exists*, not an index.
    The web-app pointer is only appended when a URL is configured — a
    dangling "see the app" with nothing to point at reads as broken.
    """
    intro = (
        "GameTrace obserwuje Twoją aktywność na Discordzie i automatycznie "
        "zapisuje, w co grasz — bez żadnej pracy z Twojej strony. Wystarczy, "
        "że grasz, a sesje same się tu pojawiają."
    )
    if not settings.gametrace_web_url:
        return intro
    return (
        f"{intro}\n\n"
        f"Pełny obraz — statystyki, historia, biblioteka gier — czeka w "
        f"aplikacji webowej: {settings.gametrace_web_url}"
    )


def recent_reply(*, sessions: list[dict], user_timezone: str | None) -> str:
    # Defensive: the API request already excludes ONGOING (status=COMPLETED,ERROR),
    # but a command whose whole purpose is "is this recording me correctly?" must
    # never show an in-progress session as if it were done.
    visible = [s for s in sessions if s.get("status") != "ONGOING"]
    if not visible:
        return recent_empty()

    tz = _resolve_tz(user_timezone)
    lines = ["Ostatnie sesje:", ""]
    for entry in visible[:5]:
        lines.append(_format_session_line(entry, tz))

    lines.append("")
    lines.append(f"Zobacz więcej w aplikacji.{_web_hint()}")
    return "\n".join(lines)
