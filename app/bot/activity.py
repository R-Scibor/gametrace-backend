"""Playing activity from a Discord member."""

from datetime import datetime

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


def _playing(member: discord.Member) -> discord.Game | discord.Activity | None:
    for activity in member.activities:
        if isinstance(activity, discord.Game) and activity.name:
            return activity
        if (
            isinstance(activity, discord.Activity)
            and activity.type == discord.ActivityType.playing
            and activity.name
        ):
            return activity
    return None


def playing_activity(member: discord.Member) -> tuple[str, str | None] | None:
    """First playing activity as ``(name, application_id)``, or None.

    A ``discord.Game`` has no application id. The id is not a second key.
    """
    activity = _playing(member)
    if activity is None:
        return None
    name = activity.name
    if not name:
        return None
    if isinstance(activity, discord.Game):
        return name, None
    return name, usable_application_id(getattr(activity, "application_id", None))


def playing_started_at(member: discord.Member) -> datetime | None:
    """When Discord says the first playing activity started (``timestamps.start``), or None."""
    activity = _playing(member)
    if activity is None:
        return None
    start = getattr(activity, "start", None)
    return start if isinstance(start, datetime) else None
