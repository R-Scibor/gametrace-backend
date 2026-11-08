"""Polish user-facing copy for Discord bot slash commands.

Kept separate from command handlers (app/bot/commands.py) so tone and
wording can be reviewed in one place, independent of the API-calling logic
around them. Every string here ends up as an ephemeral Discord reply.
"""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings

NOT_REGISTERED = "Nie jesteś zarejestrowany. Użyj `/register` lub `/login`, aby zacząć."

STATS_FAILURE = "Nie udało się pobrać statystyk. Spróbuj ponownie za chwilę."

RECENT_FAILURE = "Nie udało się pobrać ostatnich sesji. Spróbuj ponownie za chwilę."

LINK_CODES_UNCONFIGURED = "Kody logowania nie są skonfigurowane."

MENU_ACTION_FAILED = "Nie udało się. Spróbuj ponownie za chwilę."


def _day_word(days_left: int) -> str:
    # Simplified Polish plural: exact grammar needs 1 / 2-4 / 5+ (with
    # 12-14 exceptions), but days_left is always a small positive ceiling
    # here and "dzień" vs "dni" reads fine without the full declension.
    return "dzień" if days_left == 1 else "dni"


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


# Shared with the panel menu. The slash reply adds the `/stats` closer;
# the menu must not, because that channel cannot send slash commands.
_STATS_QUIET_PROMISE = (
    "Jeszcze nie mam żadnych sesji z ostatnich 7 dni, ale już Cię obserwuję — "
    "zagraj w cokolwiek, a Twój czas zacznie się tu pojawiać."
)


def stats_empty() -> str:
    """First-run / quiet-week state. Must read as a promise, not a dead end.

    Deliberately does NOT append the web hint — callers append the
    pending-errors / review-count lines (each with their own web hint) when
    those counts are non-zero, and a bare empty state still ends clean
    without one.
    """
    return f"{_STATS_QUIET_PROMISE} Wróć po sesji i sprawdź `/stats` ponownie."


def _pending_errors_sentence(count: int, *, quiet: bool) -> str:
    lead = "Poza tym masz" if quiet else "Masz"
    return (
        f"{lead} {count} sesje wymagające poprawy — "
        f"popraw je w aplikacji.{_web_hint()}"
    )


def _review_sentence(count: int, *, quiet: bool) -> str:
    if quiet:
        return (
            f"{count} gier czeka na potwierdzenie w aplikacji — "
            f"stąd też się nie liczą.{_web_hint()}"
        )
    return f"{count} gier czeka na potwierdzenie w aplikacji.{_web_hint()}"


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
            lines.append(_pending_errors_sentence(pending_errors_count, quiet=True))

        if review_count > 0:
            lines.append("")
            lines.append(_review_sentence(review_count, quiet=True))

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
        lines.append(_pending_errors_sentence(pending_errors_count, quiet=False))

    if review_count > 0:
        lines.append("")
        lines.append(_review_sentence(review_count, quiet=False))

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


def pending_deletion_reply(
    *, purge_at: str, days_left: int, user_timezone: str | None = None
) -> str:
    """Shared `/stats` and `/recent` reply when the API's bot-credential path
    403s because the account is scheduled for deletion (see
    `api_client.PendingDeletionError`). Explains the state instead of the
    bare "returned 403" a caller would otherwise surface.

    Cancelling is app-only (`DELETE /profile/me/deletion`) — there's no bot
    slash command for it — so this points at the app rather than claiming a
    Discord-side fix.
    """
    tz = _resolve_tz(user_timezone)
    dt = _parse_iso(purge_at)
    date_str = dt.astimezone(tz).strftime("%d.%m.%Y") if dt else "?"

    return (
        f"To konto jest zaplanowane do usunięcia — dane zostaną trwale "
        f"skasowane {date_str} (za {days_left} {_day_word(days_left)}).\n\n"
        f"Żeby to anulować, zaloguj się w aplikacji i cofnij usunięcie "
        f"konta w ustawieniach.{_web_hint()}"
    )


def register_pending_deletion_reply(
    *, purge_at: str, days_left: int, user_timezone: str | None = None
) -> str:
    """`/register` reply for an account that still exists but is scheduled
    for deletion — deliberately NOT `register_reply(created=False)`, since
    "already registered" would misleadingly suggest the account is in
    normal standing.
    """
    tz = _resolve_tz(user_timezone)
    dt = _parse_iso(purge_at)
    date_str = dt.astimezone(tz).strftime("%d.%m.%Y") if dt else "?"

    return (
        f"To konto jest zaplanowane do usunięcia — dane zostaną trwale "
        f"skasowane {date_str} (za {days_left} {_day_word(days_left)}). "
        f"Rejestracja nie jest tu potrzebna.\n\n"
        f"Żeby dalej korzystać z konta, zaloguj się w aplikacji i cofnij "
        f"usunięcie w ustawieniach.{_web_hint()}"
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
    """Landing copy for the panel message itself — short bilingual pitch.
    No instructions to type or click anything: this is the read-only channel
    the whole feature exists to work around.

    Does NOT restate `PANEL_TITLE`: the view this feeds (`reply_view()` in
    a later task) already renders the title as a `### heading` above the
    body, so opening the body with the title again would double it up.

    Deliberately stays compact — the full walkthrough lives behind the
    **🇵🇱 Co to jest?** / **🇬🇧 What is it?** buttons (`panel_info_pl()` /
    `panel_info_en()`), each with its own 4000-char ephemeral budget.
    """
    return (
        "🇵🇱 Bot rozpoznaje Twój status „W grze…” na Discordzie i sam "
        "zapisuje sesje. Nic nie trzeba włączać ręcznie.\n\n"
        "🇬🇧 The bot recognises your “Playing…” status on Discord and saves "
        "your sessions. Nothing to switch on."
    )


def panel_disclosure() -> str:
    """Shown before account creation, behind the **▶ START** button —
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
    return "Cześć. Co chcesz zrobić?"


# The two "Ważne" / "Important" sentences are split so each info card can
# show one of them. `panel_info_pl()` / `panel_info_en()` join them back
# under the same heading; do not rewrite either sentence.
_PL_SERVER_CHANNELS = "Komendy slash działają tylko na kanałach serwera, nie na priv. (DM)."
_PL_EXPIRED_CODE = (
    "Jeśli kod wygasł, kliknij **🔑 Weź kod** ponownie i użyj nowego w ciągu 5 minut."
)
_EN_SERVER_CHANNELS = "Slash commands only work on server channels, not in DMs."
_EN_EXPIRED_CODE = (
    "If the code expired, click **🔑 Weź kod** again and use the new one within 5 minutes."
)

_PL_WHAT = (
    "GameTrace to tracker czasu spędzonego w grach. Bot Discorda widzi "
    "Twoją aktywność (Activity / „W grze…”) i automatycznie zapisuje "
    "sesje rozgrywki. Potem przeglądasz je na stronie lub w natywnej "
    "aplikacji na Androida: biblioteka gier, szczegółowe statystyki, "
    "mapa aktywności oraz ręczne i głosowe dodawanie sesji."
)
_EN_WHAT = (
    "GameTrace is a playtime tracker. The Discord bot sees your "
    "activity (Activity / “Playing…”) and automatically logs your "
    "gaming sessions. You then review them on the website or in the "
    "native Android app: game library, detailed stats, an activity "
    "heatmap, and manual and voice session logging."
)

_PL_COMMANDS = (
    "Na tym kanale pisanie jest wyłączone, więc korzystasz z przycisków "
    "powyżej. Na kanałach serwera, gdzie możesz pisać, działają też:\n"
    "`/login` — kod logowania (ważny 5 min)\n"
    "`/register` — utworzenie konta\n"
    "`/logout` — unieważnia sesje w aplikacji"
)
_EN_COMMANDS = (
    "Writing is disabled on this channel, so use the buttons above. "
    "On server channels where you can type, these also work:\n"
    "`/login` — login code (valid 5 min)\n"
    "`/register` — create an account\n"
    "`/logout` — invalidates your app sessions"
)


def _pl_steps(web: str) -> str:
    if not web:
        return (
            "1. Musisz być na tym serwerze — bot śledzi tylko członków "
            "serwera, na którym działa.\n"
            "2. Włącz w Discordzie wyświetlanie statusu aktywności gry "
            "(Activity Status).\n"
            "3. Kliknij **▶ START** na panelu powyżej, a potem **🔑 Weź "
            "kod** — bot odpowie 6-cyfrowym kodem widocznym tylko dla "
            "Ciebie, ważnym 5 minut.\n"
            "4. Wpisz ten kod w aplikacji.\n"
            "5. Graj jak zwykle — sesje pojawią się automatycznie."
        )
    return (
        "1. Musisz być na tym serwerze — bot śledzi tylko członków "
        "serwera, na którym działa.\n"
        "2. Włącz w Discordzie wyświetlanie statusu aktywności gry "
        "(Activity Status).\n"
        "3. Kliknij **▶ START** na panelu powyżej, a potem **🔑 Weź "
        "kod** — bot odpowie 6-cyfrowym kodem widocznym tylko dla Ciebie, "
        "ważnym 5 minut.\n"
        f"4. Wpisz ten kod w aplikacji (przeglądarka {web} albo kreator "
        f"{web}/welcome).\n"
        "5. Graj jak zwykle — sesje pojawią się automatycznie w wersji web "
        "i mobilnej."
    )


def _en_steps(web: str) -> str:
    if not web:
        return (
            "1. You need to be a member of this server — the bot only "
            "tracks members of the server it runs on.\n"
            "2. Turn on Activity Status sharing in Discord.\n"
            "3. Click **▶ START** on the panel above, then **🔑 Weź "
            "kod** — the bot will reply with a 6-digit code visible only "
            "to you, valid for 5 minutes.\n"
            "4. Enter that code in the app.\n"
            "5. Play as usual — sessions will show up automatically."
        )
    return (
        "1. You need to be a member of this server — the bot only tracks "
        "members of the server it runs on.\n"
        "2. Turn on Activity Status sharing in Discord.\n"
        "3. Click **▶ START** on the panel above, then **🔑 Weź kod** "
        "— the bot will reply with a 6-digit code visible only to you, "
        "valid for 5 minutes.\n"
        f"4. Enter that code in the app (browser {web} or the wizard "
        f"{web}/welcome).\n"
        "5. Play as usual — sessions will show up automatically on web "
        "and mobile."
    )


def info_what_pl(web_url_set: bool) -> str:
    """Tracker paragraph, plus the wizard line when a web URL is set."""
    if not web_url_set:
        return _PL_WHAT
    web = settings.gametrace_web_url
    wizard = f"👉 Zacznij tutaj: {web}/welcome — kreator przeprowadzi Cię krok po kroku."
    return f"{_PL_WHAT}\n\n{wizard}"


def info_start_pl(web_url_set: bool) -> str:
    """Numbered steps, plus the expired-code sentence."""
    web = settings.gametrace_web_url if web_url_set else ""
    return f"{_pl_steps(web)}\n\n{_PL_EXPIRED_CODE}"


def info_apps_pl() -> str:
    """Web / Android lines. Only shown when a web URL is configured."""
    web = settings.gametrace_web_url
    return (
        f"Web: {web} · Android (APK): {web}/download · Onboarding od "
        f"zera: {web}/welcome.\n"
        "Jedno konto Discord — te same sesje w przeglądarce i w aplikacji."
    )


def info_commands_pl() -> str:
    """Command list, the no-typing note, and the server-channel sentence."""
    return f"{_PL_COMMANDS}\n\n{_PL_SERVER_CHANNELS}"


def info_what_en(web_url_set: bool) -> str:
    """English mirror of `info_what_pl`."""
    if not web_url_set:
        return _EN_WHAT
    web = settings.gametrace_web_url
    wizard = f"👉 Start here: {web}/welcome — the wizard walks you through it step by step."
    return f"{_EN_WHAT}\n\n{wizard}"


def info_start_en(web_url_set: bool) -> str:
    """English mirror of `info_start_pl`."""
    web = settings.gametrace_web_url if web_url_set else ""
    return f"{_en_steps(web)}\n\n{_EN_EXPIRED_CODE}"


def info_apps_en() -> str:
    """English mirror of `info_apps_pl`."""
    web = settings.gametrace_web_url
    return (
        f"Web: {web} · Android (APK): {web}/download · Onboarding from "
        f"scratch: {web}/welcome.\n"
        "One Discord account — the same sessions in your browser and in "
        "the app."
    )


def info_commands_en() -> str:
    """English mirror of `info_commands_pl`."""
    return f"{_EN_COMMANDS}\n\n{_EN_SERVER_CHANNELS}"


def panel_info_pl() -> str:
    """Full Polish info screen, opened ephemerally from the **🇵🇱 Co to
    jest?** button. Gets its own 4000-char Components V2 budget, so unlike
    `panel_body()` this can afford the full walkthrough.

    Step 3 of "Jak zacząć" is the buttons (**▶ START**, **🔑 Weź kod**) —
    never "type /login" — because this screen is reachable from the exact
    channel where typing a slash command is impossible. The "Komendy"
    section still *lists* `/login` / `/register` / `/logout` as reference:
    those work fine in DMs and unlocked channels, framed explicitly as such.

    Panel buttons show one section at a time via `info_*_pl`. This function
    still joins every section, including both "Ważne" sentences.
    """
    web = settings.gametrace_web_url
    what = f"**Co to jest?**\n{_PL_WHAT}"
    how = f"**Jak zacząć**\n{_pl_steps(web)}"
    commands_section = f"**Komendy**\n{_PL_COMMANDS}"
    important = f"**Ważne**\n{_PL_SERVER_CHANNELS} {_PL_EXPIRED_CODE}"
    if not web:
        return f"{what}\n\n{how}\n\n{commands_section}\n\n{important}"
    wizard = f"👉 Zacznij tutaj: {web}/welcome — kreator przeprowadzi Cię krok po kroku."
    apps = f"**Aplikacje**\n{info_apps_pl()}"
    return f"{what}\n\n{wizard}\n\n{how}\n\n{apps}\n\n{commands_section}\n\n{important}"


def panel_info_en() -> str:
    """English mirror of `panel_info_pl()`, opened from the **🇬🇧 What is
    it?** button. Button labels stay in their exact on-screen form
    (**▶ START**, **🔑 Weź kod**) — `🔑 Weź kod` is still Polish on the
    button itself, so an English reader needs the literal text to find it,
    not a translated paraphrase.
    """
    web = settings.gametrace_web_url
    what = f"**What is it?**\n{_EN_WHAT}"
    how = f"**How to get started**\n{_en_steps(web)}"
    commands_section = f"**Commands**\n{_EN_COMMANDS}"
    important = f"**Important**\n{_EN_SERVER_CHANNELS} {_EN_EXPIRED_CODE}"
    if not web:
        return f"{what}\n\n{how}\n\n{commands_section}\n\n{important}"
    wizard = f"👉 Start here: {web}/welcome — the wizard walks you through it step by step."
    apps = f"**Apps**\n{info_apps_en()}"
    return f"{what}\n\n{wizard}\n\n{how}\n\n{apps}\n\n{commands_section}\n\n{important}"


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


# --- Panel menu cards -----------------------------------------------------
#
# Ephemeral cards opened from the onboarding panel. Kept separate from the
# slash replies above: the view paints `accent_body` (red) on its own, so
# these builders must not be derived by parsing those sentences.


@dataclass(frozen=True)
class MenuCard:
    title: str
    caption: str | None
    body: str
    accent_body: str


def menu_text(title: str, body: str) -> MenuCard:
    return MenuCard(title=title, caption=None, body=body, accent_body=body)


def menu_loading(title: str) -> MenuCard:
    return MenuCard(title=title, caption="Chwila", body="Pobieram…", accent_body="")


def menu_failed(title: str) -> MenuCard:
    return MenuCard(
        title=title,
        caption=None,
        body=MENU_ACTION_FAILED,
        accent_body=MENU_ACTION_FAILED,
    )


def menu_code(code: str, created: bool) -> MenuCard:
    lines = [f"# {code}", "-# Wpisz go w aplikacji."]
    if settings.gametrace_web_url:
        lines.append(settings.gametrace_web_url)
    if created:
        lines.append("")
        lines.append("Witaj w GameTrace! Jeszcze nie miałeś konta, więc właśnie je założyłem.")
        lines.append("")
        lines.append("**Co dalej**")
        lines.append("1. Wpisz kod w aplikacji lub na stronie, żeby połączyć konto.")
        lines.append(
            "2. Graj jak zwykle na Discordzie — sesje zaczną się zapisywać automatycznie."
        )
        if settings.gametrace_web_url:
            lines.append("")
            lines.append(
                f"👉 Aplikacja: {settings.gametrace_web_url}/welcome — kreator "
                f"przeprowadzi Cię krok po kroku."
            )
    return MenuCard(
        title="Kod logowania",
        caption="Ważny 5 minut",
        body="\n".join(lines),
        accent_body="",
    )


def menu_stats(
    total_seconds: int,
    per_game: list[dict],
    pending_errors_count: int,
    review_count: int,
) -> MenuCard:
    quiet = total_seconds <= 0 or not per_game
    if quiet:
        parts = [_STATS_QUIET_PROMISE]
    else:
        game_lines = [
            f"- {entry['game_name']} — {_format_duration(entry['total_seconds'])}"
            for entry in per_game[:3]
        ]
        parts = [
            f"# {_format_duration(total_seconds)}\n-# Łącznie\n\n**Najwięcej grane**\n"
            + "\n".join(game_lines)
        ]

    if pending_errors_count > 0:
        parts.append(_pending_errors_sentence(pending_errors_count, quiet=quiet))

    if review_count > 0:
        parts.append(_review_sentence(review_count, quiet=quiet))

    return MenuCard(
        title="Statystyki",
        caption="Ostatnie 7 dni",
        body="\n\n".join(parts),
        accent_body="",
    )


def menu_recent(sessions: list[dict], user_timezone: str | None) -> MenuCard:
    visible = [entry for entry in sessions if entry.get("status") != "ONGOING"]
    if not visible:
        body = recent_empty()
    else:
        tz = _resolve_tz(user_timezone)
        lines = [_format_session_line(entry, tz) for entry in visible[:5]]
        lines.append("")
        lines.append(f"Zobacz więcej w aplikacji.{_web_hint()}")
        body = "\n".join(lines)
    return MenuCard(
        title="Ostatnie sesje",
        caption="Pięć ostatnich",
        body=body,
        accent_body="",
    )


def menu_logout(count: int) -> MenuCard:
    return MenuCard(
        title="Wylogowano",
        caption="Aplikacja odłączona",
        body=(
            "Tokeny logowania w aplikacji zostały unieważnione.\n"
            f"-# {count} tokenów"
        ),
        accent_body="",
    )


def menu_logout_unregistered() -> MenuCard:
    return MenuCard(
        title="Wylogowano",
        caption=None,
        body="Nie jesteś zarejestrowany.",
        accent_body="",
    )
