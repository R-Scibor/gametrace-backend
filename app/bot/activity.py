"""Playing activity from a Discord member."""

import discord

_MIN_APPLICATION_ID = 10**16


def usable_application_id(value: object) -> str | None:
    """Decimal string for a Discord snowflake. Anything smaller, including 1, is null.

    ``bool`` is an ``int``. It is rejected before the numeric check.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < _MIN_APPLICATION_ID:
        return None
    return str(value)


def playing_activity(member: discord.Member) -> tuple[str, str | None] | None:
    """First playing activity as ``(name, application_id)``, or None.

    A ``discord.Game`` has no application id. The id is not a second key.
    """
    for activity in member.activities:
        if isinstance(activity, discord.Game):
            return activity.name, None
        if (
            isinstance(activity, discord.Activity)
            and activity.type == discord.ActivityType.playing
        ):
            return activity.name, usable_application_id(
                getattr(activity, "application_id", None)
            )
    return None


def activity_name(member: discord.Member) -> str | None:
    """Name half of ``playing_activity``. Removed once callers pass the pair."""
    found = playing_activity(member)
    if found is None:
        return None
    return found[0]
