from unittest.mock import MagicMock

import discord

from app.bot.activity import playing_activity, usable_application_id


def _member_with(*activities) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.activities = list(activities)
    return member


def test_small_bool_and_missing_ids_are_null():
    assert usable_application_id(1) is None
    assert usable_application_id(10**16 - 1) is None
    assert usable_application_id(True) is None
    assert usable_application_id(False) is None
    assert usable_application_id(None) is None
    assert usable_application_id("1314682894106497096") is None


def test_snowflake_is_stored_as_a_decimal_string():
    assert usable_application_id(10**16) == "10000000000000000"
    assert usable_application_id(1314682894106497096) == "1314682894106497096"


def test_discord_game_has_no_application_id():
    member = _member_with(discord.Game(name="Minecraft"))
    assert playing_activity(member) == ("Minecraft", None)


def test_playing_activity_reads_the_first_playing_activity():
    snowflake = 10**16
    member = _member_with(
        discord.Activity(type=discord.ActivityType.listening, name="Spotify"),
        discord.Activity(
            type=discord.ActivityType.playing,
            name="Hades",
            application_id=snowflake,
        ),
    )
    assert playing_activity(member) == ("Hades", str(snowflake))


def test_a_game_before_an_activity_wins_and_stores_nothing():
    member = _member_with(
        discord.Game(name="Hades"),
        discord.Activity(
            type=discord.ActivityType.playing,
            name="Hades",
            application_id=10**16,
        ),
    )
    assert playing_activity(member) == ("Hades", None)


def test_no_playing_activity_returns_none():
    member = _member_with(
        discord.Activity(type=discord.ActivityType.listening, name="Spotify"),
    )
    assert playing_activity(member) is None
