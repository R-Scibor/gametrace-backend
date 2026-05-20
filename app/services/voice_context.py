"""
Helpers that build the per-request context for the voice transcribe prompt.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.game import Game, GameAlias, UserGamePreference
from app.models.session import GameSession, SessionStatus

logger = logging.getLogger(__name__)

CANDIDATE_FLOOR = 50
CANDIDATE_LIMIT = 3


def resolve_timezone(tz_name: str | None) -> ZoneInfo:
    fallback = settings.default_timezone or "UTC"
    if not tz_name or tz_name == "UTC":
        try:
            return ZoneInfo(fallback)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "voice: invalid user timezone %r, falling back to %s", tz_name, fallback
        )
        try:
            return ZoneInfo(fallback)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")


def build_datetime_block(tz_name: str | None, now: datetime | None = None) -> str:
    tz = resolve_timezone(tz_name)
    local_now = (now or datetime.now(tz)).astimezone(tz)
    return (
        f"Current date and time (user's timezone):\n"
        f"  ISO: {local_now.isoformat()}\n"
        f"  Timezone: {tz.key}\n"
        f"  Day of week: {local_now.strftime('%A')}\n"
        f"  Date: {local_now.strftime('%Y-%m-%d')}\n"
        f"Resolve relative references (\"yesterday\", \"wczoraj\", \"this morning\", "
        f"\"an hour ago\", \"pół godziny temu\") against this anchor."
    )


async def fetch_user_library(db: AsyncSession, user_id: str) -> list[str]:
    """
    Distinct game names + aliases the user has session history for,
    excluding soft-deleted sessions and ignored games.
    """
    name_q = (
        select(Game.primary_name)
        .join(GameSession, GameSession.game_id == Game.id)
        .outerjoin(
            UserGamePreference,
            (UserGamePreference.game_id == Game.id)
            & (UserGamePreference.user_id == user_id),
        )
        .where(
            GameSession.user_id == user_id,
            GameSession.deleted_at.is_(None),
            GameSession.status != SessionStatus.ERROR,
            (UserGamePreference.is_ignored.is_(None))
            | (UserGamePreference.is_ignored.is_(False)),
        )
        .distinct()
    )
    alias_q = (
        select(GameAlias.discord_process_name)
        .join(Game, Game.id == GameAlias.game_id)
        .join(GameSession, GameSession.game_id == Game.id)
        .outerjoin(
            UserGamePreference,
            (UserGamePreference.game_id == Game.id)
            & (UserGamePreference.user_id == user_id),
        )
        .where(
            GameSession.user_id == user_id,
            GameSession.deleted_at.is_(None),
            GameSession.status != SessionStatus.ERROR,
            (UserGamePreference.is_ignored.is_(None))
            | (UserGamePreference.is_ignored.is_(False)),
        )
        .distinct()
    )
    names = {row for row in (await db.scalars(name_q)).all() if row}
    aliases = {
        row
        for row in (await db.scalars(alias_q)).all()
        if row and len(row) >= 3 and not row.isdigit()
    }
    return sorted(names | aliases)


def match_candidates(transcript: str, library: list[str]) -> list[tuple[str, float]]:
    if not library or not transcript.strip():
        return []
    results = process.extract(
        transcript.lower(),
        [g.lower() for g in library],
        scorer=fuzz.partial_ratio,
        limit=CANDIDATE_LIMIT,
        score_cutoff=CANDIDATE_FLOOR,
    )
    # process.extract returns (matched_lowered, score, index) — recover original casing
    return [(library[idx], score) for _matched, score, idx in results]


def build_candidate_block(matches: list[tuple[str, float]]) -> str:
    if not matches:
        return ""
    lines = "\n".join(f"  - {name}" for name, _score in matches)
    return (
        f"Games the user has played that may match the transcript "
        f"(ranked by phonetic similarity):\n{lines}\n"
        f"If the transcript phonetically matches one of these (Whisper often mishears "
        f"game titles), return that exact canonical name. Otherwise return what the "
        f"user said literally."
    )
