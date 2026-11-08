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
  and every class still constructs with no arguments.

All copy comes from `app/bot/replies.py`; only button labels live here.
"""
from __future__ import annotations

import logging

import discord
from discord.ui import ActionRow, Container, LayoutView

from app.bot import commands, layout, membership, replies
from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis
from app.models.user import User

logger = logging.getLogger(__name__)

# Plain product name, used for every ephemeral screen that is not about one
# specific action — the member menu and the help reply. Matches the title
# `main.py` gives `/help`.
BRAND_TITLE = "GameTrace"
DISCLOSURE_TITLE = replies.PANEL_TITLE
REGISTERED_TITLE = "Rejestracja"


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


# Message ids whose menu click is between the loading edit and the result edit.
# The interaction guard (a later task) reads this; this task only tracks it.
_menu_inflight: set[int] = set()


def _menu_card_view(card: replies.MenuCard, *, disabled: bool) -> discord.ui.LayoutView:
    # Fresh row every time: card_view sets child.disabled in place.
    return layout.card_view(
        title=card.title,
        caption=card.caption,
        body=card.body,
        accent=layout.accent_for(card.accent_body) if card.accent_body else layout.Accent.BRAND,
        row=_MemberRow(),
        buttons_disabled=disabled,
    )


async def _run_menu(interaction, title: str, work) -> None:
    message_id = getattr(getattr(interaction, "message", None), "id", None)
    await interaction.response.edit_message(
        view=_menu_card_view(replies.menu_loading(title), disabled=True)
    )
    if message_id is not None:
        _menu_inflight.add(message_id)
    try:
        card = await work()
        await interaction.edit_original_response(view=_menu_card_view(card, disabled=False))
    except Exception:
        logger.exception("menu action failed")
        try:
            await interaction.edit_original_response(
                view=_menu_card_view(replies.menu_failed(title), disabled=False)
            )
        except Exception:
            logger.exception("menu failure edit failed")
    finally:
        if message_id is not None:
            _menu_inflight.discard(message_id)


class _PanelRow(ActionRow):
    """Buttons on the public panel message."""

    @discord.ui.button(
        label="▶ START",
        custom_id="gt:panel:start",
        style=discord.ButtonStyle.primary,
    )
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Defer first: the registration lookup is a DB round-trip and Discord's
        # ack deadline is ~3s. `thinking=True` is required on a *component*
        # interaction — without it discord.py sends DEFERRED_MESSAGE_UPDATE and
        # the click shows no loading indicator at all.
        await interaction.response.defer(ephemeral=True, thinking=True)
        discord_id = str(interaction.user.id)

        async with AsyncSessionLocal() as db:
            user = await db.get(User, discord_id)

        # Read-only branch — the account is created only on explicit accept.
        view = MemberView() if user is not None else NewUserView()
        await _send_view(interaction, view)

    @discord.ui.button(
        label="🇵🇱 Co to jest?",
        custom_id="gt:panel:help",
        style=discord.ButtonStyle.secondary,
    )
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # No I/O, so no defer. Uses the panel's button-oriented help copy
        # rather than `commands.help_command()`, whose "type /login" wording
        # is unusable in the very channel this panel exists for.
        #
        # `gt:panel:help` is kept (not renamed) even though this is now the
        # Polish-only screen: panels already posted to the live server carry
        # a button with this exact custom_id, and renaming it would leave
        # those buttons dispatching nowhere.
        body = replies.panel_info_pl()
        await interaction.response.send_message(
            view=layout.reply_view(BRAND_TITLE, body, layout.accent_for(body)),
            ephemeral=True,
        )

    @discord.ui.button(
        label="🇬🇧 What is it?",
        custom_id="gt:panel:help:en",
        style=discord.ButtonStyle.secondary,
    )
    async def help_en(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # English mirror of `help` above — no I/O, so no defer.
        body = replies.panel_info_en()
        await interaction.response.send_message(
            view=layout.reply_view(BRAND_TITLE, body, layout.accent_for(body)),
            ephemeral=True,
        )


class _NewUserRow(ActionRow):
    """The single consent button shown before an account exists."""

    @discord.ui.button(
        label="✓ Akceptuję i zakładam konto",
        custom_id="gt:new:accept",
        style=discord.ButtonStyle.success,
    )
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        discord_id = str(interaction.user.id)
        username = interaction.user.name

        async with AsyncSessionLocal() as db:
            await commands.register_user(db, discord_id, username)
        await membership.refresh_user(get_redis(), interaction.client, discord_id)

        # `edit_message` IS the interaction response, so this path must not
        # defer. The disclosure becomes the member menu in place — one
        # message transformed, not a second one sent.
        await interaction.response.edit_message(
            view=MemberView(
                card=replies.MenuCard(
                    title=REGISTERED_TITLE,
                    caption=None,
                    body=replies.panel_register_confirmation(),
                    accent_body="",
                )
            )
        )


class _MemberRow(ActionRow):
    """Everything a registered user can do from the panel."""

    @discord.ui.button(
        label="🔑 Weź kod",
        custom_id="gt:menu:code",
        style=discord.ButtonStyle.primary,
    )
    async def code(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        discord_id = str(interaction.user.id)
        username = interaction.user.name

        async def work() -> replies.MenuCard:
            async with AsyncSessionLocal() as db:
                facts = await commands.issue_login_code_facts(
                    db, get_redis(), discord_id, username
                )
            if facts.unconfigured:
                return replies.menu_text(
                    title="Kod logowania", body=replies.LINK_CODES_UNCONFIGURED
                )
            await membership.refresh_user(get_redis(), interaction.client, discord_id)
            return replies.menu_code(code=facts.code, created=facts.created)

        await _run_menu(interaction, "Kod logowania", work)

    @discord.ui.button(
        label="📊 Statystyki",
        custom_id="gt:menu:stats",
        style=discord.ButtonStyle.secondary,
    )
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        discord_id = str(interaction.user.id)

        async def work() -> replies.MenuCard:
            async with AsyncSessionLocal() as db:
                facts = await commands.stats_facts(db, discord_id)
            if facts.raw_failure:
                return replies.menu_text(title="Statystyki", body=facts.raw_failure)
            return replies.menu_stats(
                total_seconds=facts.total_seconds,
                per_game=facts.per_game,
                pending_errors_count=facts.pending_errors_count,
                review_count=facts.review_count,
            )

        await _run_menu(interaction, "Statystyki", work)

    @discord.ui.button(
        label="🕒 Ostatnie",
        custom_id="gt:menu:recent",
        style=discord.ButtonStyle.secondary,
    )
    async def recent(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        discord_id = str(interaction.user.id)

        async def work() -> replies.MenuCard:
            async with AsyncSessionLocal() as db:
                facts = await commands.recent_facts(db, discord_id)
            if facts.raw_failure:
                return replies.menu_text(title="Ostatnie sesje", body=facts.raw_failure)
            return replies.menu_recent(sessions=facts.sessions, user_timezone=facts.user_timezone)

        await _run_menu(interaction, "Ostatnie sesje", work)

    @discord.ui.button(
        label="🚪 Wyloguj",
        custom_id="gt:menu:logout",
        style=discord.ButtonStyle.danger,
    )
    async def logout(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        discord_id = str(interaction.user.id)

        async def work() -> replies.MenuCard:
            async with AsyncSessionLocal() as db:
                facts = await commands.logout_user_facts(db, get_redis(), discord_id)
            if facts.unregistered:
                return replies.menu_logout_unregistered()
            return replies.menu_logout(count=facts.count)

        await _run_menu(interaction, "Wylogowano", work)


class PanelView(LayoutView):
    """The public, permanent panel message posted by `/panel`."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            _panel_container(replies.PANEL_TITLE, replies.panel_body(), _PanelRow())
        )


class NewUserView(LayoutView):
    """Ephemeral disclosure shown to someone with no account yet."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            _panel_container(DISCLOSURE_TITLE, replies.panel_disclosure(), _NewUserRow())
        )


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
    ) -> None:
        super().__init__(timeout=None)
        if card is None:
            card = replies.MenuCard(
                title=BRAND_TITLE,
                caption="Konto połączone",
                body=replies.panel_member_menu(),
                accent_body="",
            )
        rendered = _menu_card_view(card, disabled=buttons_disabled)
        container = rendered.children[0]
        rendered.remove_item(container)
        self.add_item(container)


# Registered on startup by `on_ready` in `app/bot/main.py`:
# `for cls in PERSISTENT_VIEWS: bot.add_view(cls())`.
PERSISTENT_VIEWS: tuple[type[discord.ui.LayoutView], ...] = (
    PanelView,
    NewUserView,
    MemberView,
)
