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
  `interaction`. The optional title/body arguments below are page copy, not
  user state, and every class still constructs with no arguments.

All copy comes from `app/bot/replies.py`; only button labels live here.
"""
from __future__ import annotations

import discord
from discord.ui import ActionRow, Container, LayoutView

from app.bot import commands, layout, replies
from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis
from app.models.user import User

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
    rendered.remove_item(container)
    container.add_item(row)
    return container


async def _send_reply(interaction: discord.Interaction, title: str, body: str) -> None:
    """Ephemeral V2 followup. Never pass `content=` next to a V2 view —
    discord.py sets the v2 flag and Discord rejects the message with a 400."""
    await interaction.followup.send(
        view=layout.reply_view(title, body, layout.accent_for(body)),
        ephemeral=True,
    )


class _PanelRow(ActionRow):
    """Buttons on the public panel message."""

    @discord.ui.button(
        label="▶ Zaczynam",
        custom_id="gt:panel:start",
        style=discord.ButtonStyle.primary,
    )
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Defer first: the registration lookup is a DB round-trip and Discord's
        # ack deadline is ~3s.
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        async with AsyncSessionLocal() as db:
            user = await db.get(User, discord_id)

        # Read-only branch — the account is created only on explicit accept.
        view = MemberView() if user is not None else NewUserView()
        await interaction.followup.send(view=view, ephemeral=True)

    @discord.ui.button(
        label="ℹ Co to jest?",
        custom_id="gt:panel:help",
        style=discord.ButtonStyle.secondary,
    )
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # No I/O, so no defer. Uses the panel's button-oriented help copy
        # rather than `commands.help_command()`, whose "type /login" wording
        # is unusable in the very channel this panel exists for.
        body = replies.panel_help_reply()
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

        # `edit_message` IS the interaction response, so this path must not
        # defer. The disclosure becomes the member menu in place — one
        # message transformed, not a second one sent.
        await interaction.response.edit_message(
            view=MemberView(
                title=REGISTERED_TITLE,
                body=replies.panel_register_confirmation(),
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
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        username = interaction.user.name

        async with AsyncSessionLocal() as db:
            body = await commands.issue_login_code(db, get_redis(), discord_id, username)

        await _send_reply(interaction, "Kod logowania", body)

    @discord.ui.button(
        label="📊 Statystyki",
        custom_id="gt:menu:stats",
        style=discord.ButtonStyle.secondary,
    )
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        async with AsyncSessionLocal() as db:
            body = await commands.stats_command(db, discord_id)

        await _send_reply(interaction, "Statystyki", body)

    @discord.ui.button(
        label="🕒 Ostatnie",
        custom_id="gt:menu:recent",
        style=discord.ButtonStyle.secondary,
    )
    async def recent(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        async with AsyncSessionLocal() as db:
            body = await commands.recent_command(db, discord_id)

        await _send_reply(interaction, "Ostatnie sesje", body)

    @discord.ui.button(
        label="🚪 Wyloguj",
        custom_id="gt:menu:logout",
        style=discord.ButtonStyle.danger,
    )
    async def logout(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        async with AsyncSessionLocal() as db:
            body = await commands.logout_user(db, get_redis(), discord_id)

        await _send_reply(interaction, "Wylogowano", body)


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

    `title`/`body` vary by entry point (fresh registration vs. reopening the
    menu) — never by user — so the zero-arg default still registers cleanly
    as a persistent view.
    """

    def __init__(self, *, title: str | None = None, body: str | None = None) -> None:
        super().__init__(timeout=None)
        self.add_item(
            _panel_container(
                title or BRAND_TITLE,
                body if body is not None else replies.panel_member_menu(),
                _MemberRow(),
            )
        )


# Task 6 registers these on startup: `for cls in PERSISTENT_VIEWS: bot.add_view(cls())`.
PERSISTENT_VIEWS: tuple[type[discord.ui.LayoutView], ...] = (
    PanelView,
    NewUserView,
    MemberView,
)
