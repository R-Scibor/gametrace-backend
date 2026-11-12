"""Onboarding panel views (Components V2 buttons).

Discord requires **Send Messages** to invoke a slash command, so in a
read-only onboarding channel `/login` and `/register` are unreachable for
`@everyone` — exactly where the entry path belongs. Buttons carry no such
requirement, so this module reimplements the entry path as persistent
buttons. The `/panel` command that posts `PanelView` and registers these
views on startup lives in `app/bot/main.py`.

Two rules hold this module together:

* **One owner per `custom_id`.** discord.py keys persistent dispatch on
  `(component_type, custom_id)` in a single `message_id=None` bucket, so the
  same pair on two registered views means the second `add_view()` silently
  steals the first one's callback. Hence `gt:menu:*` lives on `MemberView`
  alone; the accept path reaches it by *editing* the disclosure message into
  a `MemberView`, never by copying the button onto `NewUserView`.
* **Views are stateless.** A persistent view is instantiated once at startup
  and shared by every clicker, so per-user data is only ever read from
  `interaction`. `MemberView`'s optional card is page copy, not user state,
  and every persistent class still constructs with no arguments. Info cards
  are not persistent: each message gets a fresh `InfoCardView`.

All copy comes from `app/bot/replies.py`; only button labels live here.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord
from discord.ui import ActionRow, Container, LayoutView

from app.bot import commands, layout, membership, replies
from app.bot.interaction_guard import (
    Action,
    claim_cooldown,
    cooldown_remaining,
    release_followup,
    try_acquire_followup,
)
from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis
from app.models.user import User

logger = logging.getLogger(__name__)

# Plain product name, used for every ephemeral screen that is not about one
# specific action — the member menu and the help reply. Matches the title
# `main.py` gives `/help`.
BRAND_TITLE = "GameTrace"
DISCLOSURE_TITLE = replies.PANEL_TITLE


def _panel_container(title: str, body: str, row: ActionRow) -> Container:
    """Reuse the shared reply renderer, then append the buttons.

    `layout.reply_view` owns the title/separator/body shape for every bot
    reply; taking its container back out keeps the panel visually identical
    to slash-command replies instead of forking the layout.
    """
    rendered = layout.reply_view(title, body, layout.accent_for(body))
    container = rendered.children[0]
    # layout.reply_view() always builds exactly one Container as the root child.
    assert isinstance(container, Container)
    rendered.remove_item(container)
    container.add_item(row)
    return container


async def _send_view(interaction: discord.Interaction, view: discord.ui.LayoutView) -> None:
    """Send a view as an ephemeral followup, always asking for the message back.

    `wait=True` is what makes discord.py file this short-lived instance under
    `ViewStore._views[message_id]`. Without a message ID it would land in the
    `message_id=None` bucket — the same one `bot.add_view()` uses at startup —
    and since an ephemeral dispatchable view has its timeout forced to 15
    minutes, its later `remove_view()` would pop those `(type, custom_id)`
    keys with no identity check and deregister the panel buttons globally.

    `Webhook.send` currently forces `wait=True` for application webhooks
    (which an interaction followup always is), so today this is belt and
    braces; it is passed explicitly so the guarantee is stated at the call
    site rather than depending on a library internal. The returned message is
    unused — only the ID that reaches `ViewStore` matters.
    """
    await interaction.followup.send(view=view, ephemeral=True, wait=True)


# Message ids owned by a menu click. Set before claim_cooldown and the loading
# edit, cleared when that click finishes or loses the claim. A second click
# while the id is here defers instead of editing the card.
_menu_inflight: set[int] = set()

# Ephemeral info message id -> the view last put on it. The next card stops
# that instance before `edit_message` stores the replacement. Not the
# persistent `message_id=None` bucket: a missing id means `stop()` and drop it.
_info_views: dict[int, discord.ui.LayoutView] = {}

_INFO_TIMEOUT = 15 * 60

_INFO_LABELS: dict[tuple[str, str], str] = {
    ("pl", "what"): "Co to jest?",
    ("pl", "start"): "Jak zacząć",
    ("pl", "app"): "Aplikacja",
    ("pl", "commands"): "Komendy",
    ("en", "what"): "What is it?",
    ("en", "start"): "Get started",
    ("en", "app"): "App",
    ("en", "commands"): "Commands",
}
_INFO_SUBJECTS = ("start", "app", "commands")


def _sent_message_id(sent: object) -> int | None:
    """discord.py 2.7 `send_message` returns `InteractionCallbackResponse`.

    The ephemeral id is `message_id` (may be missing). Do not treat any other
    attribute as an id.
    """
    message_id = getattr(sent, "message_id", None)
    if isinstance(message_id, int):
        return message_id
    return None


def _remember_info_view(message_id: int | None, view: discord.ui.LayoutView) -> None:
    if not isinstance(message_id, int):
        view.stop()
        return
    _info_views[message_id] = view


def _info_web_set() -> bool:
    return bool(replies.settings.gametrace_web_url)


def _info_custom_id(lang: str, card: str, web: bool) -> str:
    suffix = "" if web else ":noweb"
    return f"gt:info:{lang}:{card}{suffix}"


def _info_body(lang: str, card: str, web: bool) -> str:
    if lang == "pl":
        if card == "what":
            return replies.info_what_pl(web)
        if card == "start":
            return replies.info_start_pl(web)
        if card == "app":
            return replies.info_apps_pl()
        return replies.info_commands_pl()
    if card == "what":
        return replies.info_what_en(web)
    if card == "start":
        return replies.info_start_en(web)
    if card == "app":
        return replies.info_apps_en()
    return replies.info_commands_en()


class _InfoButton(discord.ui.Button):
    """Info-card button. The callback is a method so mypy does not see an assignment onto `Button.callback`."""

    def __init__(self, *, lang: str, subject: str, web: bool) -> None:
        super().__init__(
            label=_INFO_LABELS[(lang, subject)],
            custom_id=_info_custom_id(lang, subject, web),
            style=(
                discord.ButtonStyle.primary
                if subject == "start"
                else discord.ButtonStyle.secondary
            ),
        )
        self._lang = lang
        self._subject = subject

    async def callback(self, interaction: discord.Interaction) -> None:
        await _edit_info(interaction, self._lang, self._subject)


def _info_row(lang: str, card: str, web: bool) -> ActionRow:
    subjects = [subject for subject in _INFO_SUBJECTS if subject != "app" or web]
    if card == "what":
        order = subjects
    else:
        order = ["what", *[subject for subject in subjects if subject != card]]
    row: ActionRow = ActionRow()
    for subject in order:
        row.add_item(_InfoButton(lang=lang, subject=subject, web=web))
    return row


class InfoCardView(LayoutView):
    """One ephemeral info card. Built fresh per message, so language is not
    state on a shared persistent instance. Not registered in `PERSISTENT_VIEWS`.
    """

    def __init__(self, lang: str, card: str) -> None:
        super().__init__(timeout=_INFO_TIMEOUT)
        web = _info_web_set()
        rendered = layout.card_view(
            title=_INFO_LABELS[(lang, card)],
            body=_info_body(lang, card, web),
            accent=layout.Accent.BRAND,
            row=_info_row(lang, card, web),
        )
        container = rendered.children[0]
        rendered.remove_item(container)
        self.add_item(container)


async def _open_info(interaction: discord.Interaction, lang: str) -> None:
    view = InfoCardView(lang, "what")
    sent = await interaction.response.send_message(view=view, ephemeral=True)
    _remember_info_view(_sent_message_id(sent), view)


async def _edit_info(interaction: discord.Interaction, lang: str, card: str) -> None:
    message = getattr(interaction, "message", None)
    message_id = getattr(message, "id", None)
    if isinstance(message_id, int):
        previous = _info_views.pop(message_id, None)
        if previous is not None:
            previous.stop()
    view = InfoCardView(lang, card)
    await interaction.response.edit_message(view=view)
    if isinstance(message_id, int):
        _info_views[message_id] = view


_PL_MENU_LABELS = {"🔑 Weź kod", "📊 Statystyki", "🕒 Ostatnie", "🚪 Wyloguj"}
_EN_MENU_LABELS = {"🔑 Get a code", "📊 Stats", "🕒 Recent", "🚪 Log out"}


def _saved_language(user: object | None) -> str:
    return "pl" if getattr(user, "language", None) == "pl" else "en"


def _button_language(interaction: discord.Interaction) -> str | None:
    message = getattr(interaction, "message", None)
    roots = getattr(message, "components", None) or []

    def language_of(node: object) -> str | None:
        label = getattr(node, "label", None)
        if label in _PL_MENU_LABELS:
            return "pl"
        if label in _EN_MENU_LABELS:
            return "en"
        children = getattr(node, "children", None)
        if children is None:
            children = getattr(node, "components", None)
        if not children:
            return None
        for child in children:
            found = language_of(child)
            if found is not None:
                return found
        return None

    for node in roots:
        found = language_of(node)
        if found is not None:
            return found
    return None


_MENU_BUTTONS: dict[tuple[str, str], tuple[str, str, discord.ButtonStyle]] = {
    ("pl", "code"): ("🔑 Weź kod", "gt:menu:code", discord.ButtonStyle.primary),
    ("pl", "stats"): ("📊 Statystyki", "gt:menu:stats", discord.ButtonStyle.secondary),
    ("pl", "recent"): ("🕒 Ostatnie", "gt:menu:recent", discord.ButtonStyle.secondary),
    ("pl", "logout"): ("🚪 Wyloguj", "gt:menu:logout", discord.ButtonStyle.danger),
    ("en", "code"): ("🔑 Get a code", "gt:menu:code", discord.ButtonStyle.primary),
    ("en", "stats"): ("📊 Stats", "gt:menu:stats", discord.ButtonStyle.secondary),
    ("en", "recent"): ("🕒 Recent", "gt:menu:recent", discord.ButtonStyle.secondary),
    ("en", "logout"): ("🚪 Log out", "gt:menu:logout", discord.ButtonStyle.danger),
}


class _MenuButton(discord.ui.Button):
    def __init__(self, *, action: str, language: str) -> None:
        label, custom_id, style = _MENU_BUTTONS[(language, action)]
        super().__init__(label=label, custom_id=custom_id, style=style)
        self._action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        await _menu_action(interaction, self._action)


def _member_row(language: str) -> ActionRow:
    row: ActionRow = ActionRow()
    for action in ("code", "stats", "recent", "logout"):
        row.add_item(_MenuButton(action=action, language=language))
    return row


def _menu_card_view(
    card: replies.MenuCard, *, disabled: bool, button_language: str
) -> discord.ui.LayoutView:
    # Fresh row every time: card_view sets child.disabled in place.
    return layout.card_view(
        title=card.title,
        caption=card.caption,
        body=card.body,
        accent=layout.accent_for(card.accent_body) if card.accent_body else layout.Accent.BRAND,
        row=_member_row(button_language),
        buttons_disabled=disabled,
    )


async def _run_menu(
    interaction,
    title: str,
    work,
    *,
    language: str,
    button_language: str,
) -> None:
    await interaction.response.edit_message(
        view=_menu_card_view(
            replies.menu_loading(title, language),
            disabled=True,
            button_language=button_language,
        )
    )
    try:
        card = await work()
        await interaction.edit_original_response(
            view=_menu_card_view(card, disabled=False, button_language=button_language)
        )
    except Exception:
        logger.exception("menu action failed")
        try:
            await interaction.edit_original_response(
                view=_menu_card_view(
                    replies.menu_failed(title, language),
                    disabled=False,
                    button_language=button_language,
                )
            )
        except Exception:
            logger.exception("menu failure edit failed")


async def _guarded_menu(
    interaction: discord.Interaction,
    action: Action,
    title: str,
    wait: Callable[[int], str],
    work: Callable[[], Awaitable[replies.MenuCard]],
    *,
    language: str,
    button_language: str,
) -> None:
    """Clock and follow-up cap, then ``_run_menu``.

    The card id is added with no await after the idle check, before
    ``claim_cooldown`` and the loading edit, and stays until this call returns.
    A lost claim drops that id and the follow-up slot once, then edits the wait
    card. ``finally`` discards again and releases only while ``acquired``.
    """
    raw_id = getattr(getattr(interaction, "message", None), "id", None)
    card_id = raw_id if isinstance(raw_id, int) else None
    discord_id = str(interaction.user.id)
    redis = get_redis()

    async def refuse(body: str) -> None:
        if card_id is not None and card_id in _menu_inflight:
            await interaction.response.defer()
            return
        await interaction.response.edit_message(
            view=_menu_card_view(
                replies.menu_text(title, body),
                disabled=False,
                button_language=button_language,
            )
        )

    remaining = await cooldown_remaining(redis, discord_id, action)
    # No await between this check and the add on the path that does the work.
    if card_id is not None and card_id in _menu_inflight:
        await interaction.response.defer()
        return
    if remaining is not None:
        await refuse(wait(remaining))
        return
    if not try_acquire_followup():
        await refuse(replies.followup_busy(language))
        return
    acquired = True
    if card_id is not None:
        _menu_inflight.add(card_id)
    try:
        lost = await claim_cooldown(redis, discord_id, action)
        if lost is not None:
            release_followup()
            acquired = False
            if card_id is not None:
                _menu_inflight.discard(card_id)
            await refuse(wait(lost))
            return
        await _run_menu(
            interaction,
            title,
            work,
            language=language,
            button_language=button_language,
        )
    finally:
        if acquired:
            release_followup()
            acquired = False
        if card_id is not None:
            _menu_inflight.discard(card_id)


class _PanelRow(ActionRow):
    """Buttons on the public panel message."""

    @discord.ui.button(
        label="▶ START",
        custom_id="gt:panel:start",
        style=discord.ButtonStyle.primary,
    )
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Cap only — no cooldown. A full cap is its own ephemeral message, not
        # an edit of the public panel and not a defer.
        if not try_acquire_followup():
            await interaction.response.send_message(
                view=layout.reply_view(BRAND_TITLE, replies.followup_busy()),
                ephemeral=True,
            )
            return
        try:
            # The registration lookup is a DB round-trip and Discord's ack
            # deadline is ~3s. `thinking=True` is required on a *component*
            # interaction — without it discord.py sends DEFERRED_MESSAGE_UPDATE
            # and the click shows no loading indicator at all.
            await interaction.response.defer(ephemeral=True, thinking=True)
            discord_id = str(interaction.user.id)

            async with AsyncSessionLocal() as db:
                user = await db.get(User, discord_id)

            # Read-only branch — the account is created only on explicit accept.
            view: LayoutView
            if user is None:
                view = NewUserView()
            else:
                view = MemberView(language=_saved_language(user))
            await _send_view(interaction, view)
        finally:
            release_followup()

    @discord.ui.button(
        label="🇵🇱 Co to jest?",
        custom_id="gt:panel:help",
        style=discord.ButtonStyle.secondary,
    )
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # No I/O, so no defer. Opens the Polish "Co to jest?" card, not the
        # whole `panel_info_pl()` wall and not `commands.help_command()`.
        #
        # `gt:panel:help` is kept (not renamed) even though this is now the
        # Polish-only screen: panels already posted to the live server carry
        # a button with this exact custom_id, and renaming it would leave
        # those buttons dispatching nowhere.
        await _open_info(interaction, "pl")

    @discord.ui.button(
        label="🇬🇧 What is it?",
        custom_id="gt:panel:help:en",
        style=discord.ButtonStyle.secondary,
    )
    async def help_en(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # English mirror of `help` above — no I/O, so no defer.
        await _open_info(interaction, "en")


class _AcceptButton(discord.ui.Button):
    def __init__(self, *, language: str) -> None:
        if language == "pl":
            super().__init__(
                label="✓ Akceptuję i zakładam konto",
                custom_id="gt:new:accept",
                style=discord.ButtonStyle.success,
            )
        else:
            super().__init__(
                label="✓ I agree and create my account",
                custom_id="gt:new:accept:en",
                style=discord.ButtonStyle.success,
            )
        self._language = language

    async def callback(self, interaction: discord.Interaction) -> None:
        await _accept_disclosure(interaction, self._language)


class _SwitchButton(discord.ui.Button):
    """Shown on one disclosure. The id is the language this press opens."""

    def __init__(self, *, target: str) -> None:
        if target == "pl":
            super().__init__(
                label="🇵🇱 Polski",
                custom_id="gt:new:lang:pl",
                style=discord.ButtonStyle.secondary,
            )
        else:
            super().__init__(
                label="🇬🇧 English",
                custom_id="gt:new:lang:en",
                style=discord.ButtonStyle.secondary,
            )
        self._target = target

    async def callback(self, interaction: discord.Interaction) -> None:
        view = NewUserPolishView() if self._target == "pl" else NewUserView()
        await interaction.response.edit_message(view=view)


def _disclosure_row(language: str) -> ActionRow:
    row: ActionRow = ActionRow()
    row.add_item(_AcceptButton(language=language))
    row.add_item(_SwitchButton(target="pl" if language == "en" else "en"))
    return row


async def _accept_disclosure(interaction: discord.Interaction, language: str) -> None:
    discord_id = str(interaction.user.id)
    username = interaction.user.name

    async with AsyncSessionLocal() as db:
        await commands.register_user(db, discord_id, username, language=language)
        user = await db.get(User, discord_id)
    await membership.refresh_user(get_redis(), interaction.client, discord_id)

    saved = user.language if user is not None and user.language == "pl" else "en"
    await interaction.response.edit_message(
        view=MemberView(
            language=saved,
            card=replies.MenuCard(
                title=replies.registered_title(saved),
                caption=None,
                body=replies.panel_register_confirmation(saved),
                accent_body="",
            ),
        )
    )


class NewUserView(LayoutView):
    """English disclosure. This is what ▶ START sends when no account exists."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            _panel_container(DISCLOSURE_TITLE, replies.panel_disclosure_en(), _disclosure_row("en"))
        )


class NewUserPolishView(LayoutView):
    """Polish disclosure. Owns the original gt:new:accept id."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            _panel_container(DISCLOSURE_TITLE, replies.panel_disclosure(), _disclosure_row("pl"))
        )


async def _menu_action(interaction: discord.Interaction, action: str) -> None:
    discord_id = str(interaction.user.id)
    username = interaction.user.name
    async with AsyncSessionLocal() as db:
        user = await db.get(User, discord_id)
    language = _saved_language(user)
    buttons = _button_language(interaction) or language

    if action == "code":
        title = "Kod logowania" if language == "pl" else "Login code"

        async def load_code() -> replies.MenuCard:
            async with AsyncSessionLocal() as db:
                facts = await commands.issue_login_code_facts(
                    db, get_redis(), discord_id, username
                )
            if facts.unconfigured:
                body = (
                    replies.LINK_CODES_UNCONFIGURED
                    if language == "pl"
                    else replies.LINK_CODES_UNCONFIGURED_EN
                )
                return replies.menu_text(title=title, body=body)
            await membership.refresh_user(get_redis(), interaction.client, discord_id)
            return replies.menu_code(code=facts.code, created=facts.created, language=language)

        await _guarded_menu(
            interaction, Action.CODE, title,
            lambda n: replies.code_wait(n, language), load_code,
            language=language, button_language=buttons,
        )
        return

    if action == "stats":
        title = "Statystyki" if language == "pl" else "Stats"

        async def load_stats() -> replies.MenuCard:
            async with AsyncSessionLocal() as db:
                facts = await commands.stats_facts(db, discord_id)
            if facts.raw_failure:
                return replies.menu_failed(title, language)
            return replies.menu_stats(
                total_seconds=facts.total_seconds,
                per_game=facts.per_game,
                pending_errors_count=facts.pending_errors_count,
                review_count=facts.review_count,
                language=language,
            )

        await _guarded_menu(
            interaction, Action.STATS, title,
            lambda n: replies.action_wait(n, language), load_stats,
            language=language, button_language=buttons,
        )
        return

    if action == "recent":
        title = "Ostatnie sesje" if language == "pl" else "Recent sessions"

        async def load_recent() -> replies.MenuCard:
            async with AsyncSessionLocal() as db:
                facts = await commands.recent_facts(db, discord_id)
            if facts.raw_failure:
                return replies.menu_failed(title, language)
            return replies.menu_recent(
                sessions=facts.sessions,
                user_timezone=facts.user_timezone,
                language=language,
            )

        await _guarded_menu(
            interaction, Action.RECENT, title,
            lambda n: replies.action_wait(n, language), load_recent,
            language=language, button_language=buttons,
        )
        return

    title = "Wylogowano" if language == "pl" else "Logged out"

    async def load_logout() -> replies.MenuCard:
        async with AsyncSessionLocal() as db:
            facts = await commands.logout_user_facts(db, get_redis(), discord_id)
        if facts.unregistered:
            return replies.menu_logout_unregistered(language)
        return replies.menu_logout(count=facts.count, language=language)

    await _guarded_menu(
        interaction, Action.LOGOUT, title,
        lambda n: replies.action_wait(n, language), load_logout,
        language=language, button_language=buttons,
    )


class PanelView(LayoutView):
    """The public, permanent panel message posted by `/panel`.

    Uses `card_view` (soft gap, then a hard gap above the buttons) rather
    than `_panel_container`. The container is moved onto this subclass so
    `bot.add_view(PanelView())` still gets a `LayoutView` with `timeout=None`.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)
        rendered = layout.card_view(
            title=replies.PANEL_TITLE,
            body=replies.panel_body(),
            accent=layout.Accent.BRAND,
            row=_PanelRow(),
        )
        container = rendered.children[0]
        rendered.remove_item(container)
        self.add_item(container)


class MemberView(LayoutView):
    """Ephemeral menu for a registered user.

    `card` varies by entry point (fresh registration vs. reopening the menu)
    — never by user — so the zero-arg default still registers cleanly as a
    persistent view. Built from a `card_view` container moved onto this
    subclass; `card_view` itself returns a plain `LayoutView`.
    """

    def __init__(
        self,
        *,
        card: replies.MenuCard | None = None,
        buttons_disabled: bool = False,
        language: str = "en",
    ) -> None:
        super().__init__(timeout=None)
        self._language = "pl" if language == "pl" else "en"
        if card is None:
            card = replies.MenuCard(
                title=BRAND_TITLE,
                caption=replies.member_menu_caption(self._language),
                body=replies.panel_member_menu(self._language),
                accent_body="",
            )
        rendered = _menu_card_view(
            card, disabled=buttons_disabled, button_language=self._language
        )
        container = rendered.children[0]
        rendered.remove_item(container)
        self.add_item(container)


# Registered on startup by `on_ready` in `app/bot/main.py`:
# `for cls in PERSISTENT_VIEWS: bot.add_view(cls())`.
PERSISTENT_VIEWS: tuple[type[discord.ui.LayoutView], ...] = (
    PanelView,
    NewUserView,
    NewUserPolishView,
    MemberView,
)
