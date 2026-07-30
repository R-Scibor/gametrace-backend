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


# Does NOT restate the product name: every consumer (`/help`, the panel help
# screen) renders a "GameTrace" title as a `### heading` right above this, so
# opening with "**GameTrace** — " printed the title twice. Same rule as
# `panel_body()`.
_HELP_WHAT_IS_IT = (
    "Tracker czasu spędzonego w grach.\n\n"
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


# --- Onboarding panel (Components V2) -------------------------------------
#
# Posted as a persistent message in a read-only channel where @everyone
# cannot Send Messages — so slash commands are unreachable there. Every
# string below must be actionable via a button, never "type this command".
# The views/buttons that render these strings are wired in a later task;
# this module only owns the copy.

PANEL_TITLE = "🎮 GameTrace"


def _privacy_hint() -> str:
    return (
        f" Pełna polityka prywatności: {settings.gametrace_privacy_url}"
        if settings.gametrace_privacy_url
        else ""
    )


def panel_body() -> str:
    """Landing copy for the panel message itself — short bilingual pitch plus
    a pointer at the buttons below. No instructions to type anything: this is
    the read-only channel the whole feature exists to work around.

    Does NOT restate `PANEL_TITLE`: the view this feeds (`reply_view()` in
    a later task) already renders the title as a `### heading` above the
    body, so opening the body with the title again would double it up.

    Deliberately stays compact — the full walkthrough lives behind the
    **🇵🇱 Co to jest?** / **🇬🇧 What is it?** buttons (`panel_info_pl()` /
    `panel_info_en()`), each with its own 4000-char ephemeral budget.
    """
    return (
        "🇵🇱 Tracker czasu spędzonego w grach — bot widzi Twój status gry na "
        "Discordzie i zapisuje sesje automatycznie. Użyj przycisków poniżej.\n\n"
        "🇬🇧 A playtime tracker — the bot sees your Discord game status and "
        "logs sessions automatically. Use the buttons below."
    )


def panel_disclosure() -> str:
    """Shown before account creation, behind the **▶ Zaczynam** button —
    plain-language disclosure of what gets recorded, not stored consent.
    Confirmed via **✓ Akceptuję i zakładam konto**.

    The privacy link (when configured) sits on its own line right after the
    "what gets recorded" paragraph, not tacked onto the CTA sentence — it
    should read as part of the disclosure, not as a run-on after "click
    here to continue".
    """
    privacy_hint = _privacy_hint()
    privacy_line = f"\n{privacy_hint.strip()}\n" if privacy_hint else ""
    return (
        "Zanim założysz konto — czym jest sesja gry:\n\n"
        "Kiedy grasz, GameTrace zapisuje **nazwę gry** oraz **czas "
        "rozpoczęcia i zakończenia** sesji. Nic więcej — żadnej treści "
        f"wiadomości, żadnych zrzutów ekranu.\n{privacy_line}\n"
        "Kliknij **✓ Akceptuję i zakładam konto**, żeby kontynuować."
    )


def panel_register_confirmation() -> str:
    """Shown right after the panel creates the account — points at the
    button that gets the login code, not at typing /login."""
    return (
        "Konto założone! Od teraz bot automatycznie zapisuje Twoje sesje "
        "grania.\n\n"
        "Kliknij **🔑 Weź kod**, żeby dostać kod i połączyć konto z aplikacją."
    )


def panel_member_menu() -> str:
    """Body of the ephemeral menu an already-registered user opens from the
    panel. Deliberately tiny: the buttons underneath are the content, and
    every extra sentence pushes them further from the eye.

    Must not restate the title (the container renders `### {title}` above
    this) and must not instruct any slash command — the panel exists exactly
    where slash commands are unreachable.
    """
    return "Cześć! Twoje konto jest już połączone. Co chcesz zrobić?"


def panel_info_pl() -> str:
    """Full Polish info screen, opened ephemerally from the **🇵🇱 Co to
    jest?** button. Gets its own 4000-char Components V2 budget, so unlike
    `panel_body()` this can afford the full walkthrough.

    Step 3 of "Jak zacząć" is the buttons (**▶ Zaczynam**, **🔑 Weź kod**) —
    never "type /login" — because this screen is reachable from the exact
    channel where typing a slash command is impossible. The "Komendy"
    section still *lists* `/login` / `/register` / `/logout` as reference:
    those work fine in DMs and unlocked channels, framed explicitly as such.
    """
    web = settings.gametrace_web_url

    what_is_it = (
        "**Co to jest?**\n"
        "GameTrace to tracker czasu spędzonego w grach. Bot Discorda widzi "
        "Twoją aktywność (Activity / „W grze…”) i automatycznie zapisuje "
        "sesje rozgrywki. Potem przeglądasz je na stronie lub w natywnej "
        "aplikacji na Androida: biblioteka gier, szczegółowe statystyki, "
        "mapa aktywności oraz ręczne i głosowe dodawanie sesji."
    )

    commands_section = (
        "**Komendy**\n"
        "Na tym kanale pisanie jest wyłączone, więc korzystasz z przycisków "
        "powyżej. Na kanałach serwera, gdzie możesz pisać, działają też:\n"
        "`/login` — kod logowania (ważny 5 min)\n"
        "`/register` — utworzenie konta\n"
        "`/logout` — unieważnia sesje w aplikacji"
    )

    important_section = (
        "**Ważne**\n"
        "Komendy slash działają tylko na kanałach serwera, nie na priv. "
        "(DM). Jeśli kod wygasł, kliknij **🔑 Weź kod** ponownie i użyj "
        "nowego w ciągu 5 minut."
    )

    if not web:
        how_to_start = (
            "**Jak zacząć**\n"
            "1. Musisz być na tym serwerze — bot śledzi tylko członków "
            "serwera, na którym działa.\n"
            "2. Włącz w Discordzie wyświetlanie statusu aktywności gry "
            "(Activity Status).\n"
            "3. Kliknij **▶ Zaczynam** na panelu powyżej, a potem **🔑 Weź "
            "kod** — bot odpowie 6-cyfrowym kodem widocznym tylko dla "
            "Ciebie, ważnym 5 minut.\n"
            "4. Wpisz ten kod w aplikacji.\n"
            "5. Graj jak zwykle — sesje pojawią się automatycznie."
        )
        return f"{what_is_it}\n\n{how_to_start}\n\n{commands_section}\n\n{important_section}"

    start_here = f"👉 Zacznij tutaj: {web}/welcome — kreator przeprowadzi Cię krok po kroku."

    how_to_start = (
        "**Jak zacząć**\n"
        "1. Musisz być na tym serwerze — bot śledzi tylko członków "
        "serwera, na którym działa.\n"
        "2. Włącz w Discordzie wyświetlanie statusu aktywności gry "
        "(Activity Status).\n"
        "3. Kliknij **▶ Zaczynam** na panelu powyżej, a potem **🔑 Weź "
        "kod** — bot odpowie 6-cyfrowym kodem widocznym tylko dla Ciebie, "
        "ważnym 5 minut.\n"
        f"4. Wpisz ten kod w aplikacji (przeglądarka {web} albo kreator "
        f"{web}/welcome).\n"
        "5. Graj jak zwykle — sesje pojawią się automatycznie w wersji web "
        "i mobilnej."
    )

    apps_section = (
        "**Aplikacje**\n"
        f"Web: {web} · Android (APK): {web}/download · Onboarding od "
        f"zera: {web}/welcome.\n"
        "Jedno konto Discord — te same sesje w przeglądarce i w aplikacji."
    )

    return (
        f"{what_is_it}\n\n{start_here}\n\n{how_to_start}\n\n{apps_section}"
        f"\n\n{commands_section}\n\n{important_section}"
    )


def panel_info_en() -> str:
    """English mirror of `panel_info_pl()`, opened from the **🇬🇧 What is
    it?** button. Button labels stay in their literal Polish form
    (**▶ Zaczynam**, **🔑 Weź kod**) — the buttons themselves are labelled in
    Polish, so an English reader needs the exact on-screen text to find them,
    not a translated paraphrase.
    """
    web = settings.gametrace_web_url

    what_is_it = (
        "**What is it?**\n"
        "GameTrace is a playtime tracker. The Discord bot sees your "
        "activity (Activity / “Playing…”) and automatically logs your "
        "gaming sessions. You then review them on the website or in the "
        "native Android app: game library, detailed stats, an activity "
        "heatmap, and manual and voice session logging."
    )

    commands_section = (
        "**Commands**\n"
        "Writing is disabled on this channel, so use the buttons above. "
        "On server channels where you can type, these also work:\n"
        "`/login` — login code (valid 5 min)\n"
        "`/register` — create an account\n"
        "`/logout` — invalidates your app sessions"
    )

    important_section = (
        "**Important**\n"
        "Slash commands only work on server channels, not in DMs. If the "
        "code expired, click **🔑 Weź kod** again and use the new one "
        "within 5 minutes."
    )

    if not web:
        how_to_start = (
            "**How to get started**\n"
            "1. You need to be a member of this server — the bot only "
            "tracks members of the server it runs on.\n"
            "2. Turn on Activity Status sharing in Discord.\n"
            "3. Click **▶ Zaczynam** on the panel above, then **🔑 Weź "
            "kod** — the bot will reply with a 6-digit code visible only "
            "to you, valid for 5 minutes.\n"
            "4. Enter that code in the app.\n"
            "5. Play as usual — sessions will show up automatically."
        )
        return f"{what_is_it}\n\n{how_to_start}\n\n{commands_section}\n\n{important_section}"

    start_here = f"👉 Start here: {web}/welcome — the wizard walks you through it step by step."

    how_to_start = (
        "**How to get started**\n"
        "1. You need to be a member of this server — the bot only tracks "
        "members of the server it runs on.\n"
        "2. Turn on Activity Status sharing in Discord.\n"
        "3. Click **▶ Zaczynam** on the panel above, then **🔑 Weź kod** "
        "— the bot will reply with a 6-digit code visible only to you, "
        "valid for 5 minutes.\n"
        f"4. Enter that code in the app (browser {web} or the wizard "
        f"{web}/welcome).\n"
        "5. Play as usual — sessions will show up automatically on web "
        "and mobile."
    )

    apps_section = (
        "**Apps**\n"
        f"Web: {web} · Android (APK): {web}/download · Onboarding from "
        f"scratch: {web}/welcome.\n"
        "One Discord account — the same sessions in your browser and in "
        "the app."
    )

    return (
        f"{what_is_it}\n\n{start_here}\n\n{how_to_start}\n\n{apps_section}"
        f"\n\n{commands_section}\n\n{important_section}"
    )


# --- /panel command (admin-only, posts the panel above) -------------------

PANEL_POSTED = "Panel opublikowany na tym kanale."

PANEL_MISSING_PERMISSIONS = (
    "Nie udało się opublikować panelu — brakuje mi uprawnienia **Wyślij "
    "wiadomości** (Send Messages) na tym kanale."
)

# Distinct from PANEL_MISSING_PERMISSIONS on purpose: this is the
# `interaction.channel is None` branch, where nothing is wrong with the bot's
# permissions and pointing an admin at Send Messages would send them chasing a
# setting that is already correct.
PANEL_CHANNEL_UNAVAILABLE = (
    "Nie udało się opublikować panelu — nie rozpoznaję kanału, z którego "
    "wywołano komendę. Spróbuj ponownie na kanale tekstowym serwera."
)

PANEL_REFUSED_NOT_ADMIN = (
    "Ta komenda wymaga uprawnienia **Zarządzaj serwerem** (Manage Server)."
)
