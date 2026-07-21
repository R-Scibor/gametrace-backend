"""Polish user-facing copy for Discord bot slash commands.

Kept separate from command handlers (app/bot/commands.py) so tone and
wording can be reviewed in one place, independent of the API-calling logic
around them. Every string here ends up as an ephemeral Discord reply.
"""
from app.core.config import settings

NOT_REGISTERED = "Nie jesteś zarejestrowany. Użyj `/register` lub `/login`, aby zacząć."

STATS_FAILURE = "Nie udało się pobrać statystyk. Spróbuj ponownie za chwilę."


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
