"""
tests/unit/test_self_healing_helpers.py

Phase 2 — pure function unit tests for app/bot/self_healing.py.
No DB, no Discord gateway — just plain logic with MagicMock stubs.
"""
from unittest.mock import MagicMock

import discord

from app.bot.self_healing import _find_member, _get_game_name


# ── _get_game_name ────────────────────────────────────────────────────────────

def _member_with(*activities) -> MagicMock:
    m = MagicMock(spec=discord.Member)
    m.activities = list(activities)
    return m


def test_discord_game_activity():
    member = _member_with(discord.Game(name="Minecraft"))
    assert _get_game_name(member) == "Minecraft"


def test_playing_type_activity():
    activity = discord.Activity(type=discord.ActivityType.playing, name="Hades")
    member = _member_with(activity)
    assert _get_game_name(member) == "Hades"


def test_non_game_activities():
    member = _member_with(
        discord.Streaming(name="Just Chatting", url="https://twitch.tv/x"),
        discord.Activity(type=discord.ActivityType.listening, name="Spotify"),
    )
    assert _get_game_name(member) is None


def test_empty_activities():
    member = _member_with()
    assert _get_game_name(member) is None


def test_first_game_activity_wins():
    member = _member_with(
        discord.Activity(type=discord.ActivityType.listening, name="Spotify"),
        discord.Game(name="Hades"),
    )
    assert _get_game_name(member) == "Hades"


# ── _find_member ──────────────────────────────────────────────────────────────

def _mock_guild(discord_id: int, member: MagicMock) -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.side_effect = lambda uid: member if uid == discord_id else None
    return guild


def test_finds_member_in_first_guild():
    member = MagicMock(spec=discord.Member)
    guild = _mock_guild(123, member)

    result = _find_member([guild], "123")
    assert result is member


def test_finds_member_in_second_guild():
    member = MagicMock(spec=discord.Member)
    guild_a = _mock_guild(999, MagicMock())   # different user
    guild_b = _mock_guild(456, member)

    result = _find_member([guild_a, guild_b], "456")
    assert result is member


def test_member_not_in_any_guild():
    guild = _mock_guild(999, MagicMock())
    assert _find_member([guild], "123") is None


def test_discord_id_is_cast_to_int():
    member = MagicMock(spec=discord.Member)
    guild = _mock_guild(789, member)

    result = _find_member([guild], "789")   # string input

    assert result is member
    guild.get_member.assert_called_with(789)  # must be int
