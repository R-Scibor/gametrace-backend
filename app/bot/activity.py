"""Playing-game name from a Discord member's activities."""

import discord


def activity_name(member: discord.Member) -> str | None:
    """Return the first playing-game name, or None when the member is not in a game."""
    for activity in member.activities:
        if isinstance(activity, discord.Game):
            return activity.name
        if (
            isinstance(activity, discord.Activity)
            and activity.type == discord.ActivityType.playing
        ):
            return activity.name
    return None
