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

LINK_CODES_UNCONFIGURED = "Kody logowania nie są skonfigurowane."


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
    """First-run / quiet-week state. Must read as a promise, not a dead end.

    Deliberately does NOT append the web hint — callers append the
    pending-errors / review-count lines (each with their own web hint) when
    those counts are non-zero, and a bare empty state still ends clean
    without one.
    """
    return (
        "Jeszcze nie mam żadnych sesji z ostatnich 7 dni, ale już Cię obserwuję — "
        "zagraj w cokolwiek, a Twój czas zacznie się tu pojawiać. "
        "Wróć po sesji i sprawdź `/stats` ponownie."
    )


def stats_reply(
    *,
    total_seconds: int,
    per_game: list[dict],
    pending_errors_count: int,
    review_count: int,
) -> str:
    if total_seconds <= 0 or not per_game:
        lines = [stats_empty()]

        if pending_errors_count > 0:
            lines.append("")
            lines.append(
                f"Poza tym masz {pending_errors_count} sesje wymagające poprawy — "
                f"popraw je w aplikacji.{_web_hint()}"
            )

        if review_count > 0:
            lines.append("")
            lines.append(
                f"{review_count} gier czeka na potwierdzenie w aplikacji — "
                f"stąd też się nie liczą.{_web_hint()}"
            )

        return "\n".join(lines)

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
    """First-run / quiet-history state. Same promise-not-dead-end bar as /stats.

    Must not claim zero sessions were recorded — the API request behind
    `/recent` filters to library-visible games, so a NEEDS_REVIEW stub can
    leave this list empty even though sessions do exist server-side.
    """
    return (
        "Nie mam tu jeszcze nic do pokazania, ale już Cię obserwuję — "
        f"zagraj w cokolwiek, a pojawi się tutaj po zakończeniu.{_web_hint()}"
    )


_HELP_WHAT_IS_IT = (
    "**GameTrace** — tracker czasu spędzonego w grach.\n\n"
    "Bot widzi Twoją aktywność na Discordzie (status „W grze…”) i "
    "automatycznie zapisuje sesje rozgrywki."
)

_HELP_HOW_TO_START_NO_APP = (
    "**Jak zacząć**\n"
    "1. Włącz w Discordzie wyświetlanie statusu aktywności gry (Activity Status).\n"
    "2. Na tym kanale wpisz `/login` albo `/register` — bot odpowie kodem "
    "widocznym tylko dla Ciebie."
)

_HELP_COMMANDS = (
    "**Komendy**\n"
    "`/register` — załóż konto i zacznij śledzenie sesji\n"
    "`/login` — kod logowania do aplikacji (ważny 5 min)\n"
    "`/logout` — wyloguj się ze wszystkich urządzeń w aplikacji\n"
    "`/stats` — łączny czas grania i najczęściej grane gry z ostatnich 7 dni\n"
    "`/recent` — ostatnie sesje: gra, kiedy grałeś i ile trwało\n"
    "`/help` — czym jest GameTrace i jak zacząć"
)


def help_reply() -> str:
    """Orientation for someone who noticed the bot and has no idea what it is.

    Spells out every command here even though Discord's own slash-command
    picker also lists them — the picker's descriptions are one-liners
    truncated to fit, this is the place for real explanations. The
    web-app section (wizard link, app links) is only appended when a URL is
    configured — a dangling "see the app" with nothing to point at reads as
    broken.
    """
    if not settings.gametrace_web_url:
        return f"{_HELP_WHAT_IS_IT}\n\n{_HELP_HOW_TO_START_NO_APP}\n\n{_HELP_COMMANDS}"

    web_url = settings.gametrace_web_url
    return (
        f"{_HELP_WHAT_IS_IT} W aplikacji webowej i na Androidzie masz "
        f"bibliotekę gier, szczegółowe statystyki, mapę aktywności oraz "
        f"ręczne i głosowe dodawanie sesji.\n\n"
        f"👉 Zacznij tutaj: {web_url}/welcome — kreator przeprowadzi Cię "
        f"krok po kroku.\n\n"
        f"**Jak zacząć**\n"
        f"1. Włącz w Discordzie wyświetlanie statusu aktywności gry (Activity Status).\n"
        f"2. Na tym kanale wpisz `/login` albo `/register` — bot odpowie kodem "
        f"widocznym tylko dla Ciebie.\n"
        f"3. Wpisz kod w aplikacji i graj jak zwykle — sesje pojawią się automatycznie.\n\n"
        f"{_HELP_COMMANDS}\n\n"
        f"**Aplikacje**\n"
        f"• Web: {web_url}\n"
        f"• Android: {web_url}/download\n\n"
        f"Komendy slash działają tylko na kanałach serwera — nie na priv. (DM)."
    )


def register_reply(*, created: bool) -> str:
    """First-time /register gets full orientation; returning users get a
    terse ack plus a reminder of the /login next step — not a bare no-op
    reply, since "already registered" still leaves "now what?" unanswered.
    """
    if not created:
        if not settings.gametrace_web_url:
            return "Jesteś już zarejestrowany. Wpisz `/login`, żeby dostać kod logowania do aplikacji."
        return (
            f"Jesteś już zarejestrowany. Wpisz `/login` i zaloguj się w "
            f"aplikacji lub na stronie: {settings.gametrace_web_url}"
        )

    intro = (
        "Zarejestrowano w GameTrace! Od teraz bot automatycznie zapisuje "
        "Twoje sesje grania.\n\n"
        "**Co dalej**\n"
        "1. Wpisz `/login` — dostaniesz kod widoczny tylko dla Ciebie.\n"
        "2. Wpisz kod w aplikacji, żeby połączyć konto.\n"
        "3. Graj jak zwykle — sesje pojawią się automatycznie."
    )
    if not settings.gametrace_web_url:
        return intro
    return (
        f"{intro}\n\n"
        f"👉 Aplikacja: {settings.gametrace_web_url}/welcome — kreator "
        f"przeprowadzi Cię krok po kroku."
    )


def login_reply(*, code: str, created: bool) -> str:
    """`/login` upserts the user itself, so `created=True` here means "this
    is this person's first contact with the bot at all" — same onboarding
    treatment as /register's first-time path. Returning users get the code
    plus a link, not the full walkthrough again."""
    if not created:
        if not settings.gametrace_web_url:
            return f"Twój kod logowania: **{code}**. Wpisz go w aplikacji w ciągu 5 minut."
        return (
            f"Twój kod logowania: **{code}**. Wpisz go w aplikacji lub na "
            f"stronie w ciągu 5 minut: {settings.gametrace_web_url}"
        )

    intro = (
        "Witaj w GameTrace! Jeszcze nie miałeś konta, więc właśnie je "
        "założyłem.\n\n"
        f"Twój kod logowania: **{code}**. Ważny 5 minut.\n\n"
        "**Co dalej**\n"
        "1. Wpisz kod w aplikacji lub na stronie, żeby połączyć konto.\n"
        "2. Graj jak zwykle na Discordzie — sesje zaczną się zapisywać "
        "automatycznie."
    )
    if not settings.gametrace_web_url:
        return intro
    return (
        f"{intro}\n\n"
        f"👉 Aplikacja: {settings.gametrace_web_url}/welcome — kreator "
        f"przeprowadzi Cię krok po kroku."
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
