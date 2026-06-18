from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import EnrichmentStatus, UserGamePreference
from app.models.session import GameSession
from app.services.session_visibility import visible_session


async def _session_owner_ids(db: AsyncSession, game_id: int) -> list[str]:
    result = await db.execute(
        select(GameSession.user_id)
        .where(GameSession.game_id == game_id, *visible_session())
        .distinct()
    )
    return list(result.scalars().all())


async def mark_needs_review_inbox(db: AsyncSession, game_id: int) -> None:
    """Insert inbox rows for session owners; never overwrite an existing preference."""
    for user_id in await _session_owner_ids(db, game_id):
        stmt = (
            pg_insert(UserGamePreference)
            .values(user_id=user_id, game_id=game_id, is_accepted=False)
            .on_conflict_do_nothing(index_elements=["user_id", "game_id"])
        )
        await db.execute(stmt)


async def clear_review_on_enriched(db: AsyncSession, game_id: int) -> None:
    """Drop review state once enrichment succeeds; leave is_ignored untouched."""
    await db.execute(
        update(UserGamePreference)
        .where(UserGamePreference.game_id == game_id)
        .values(is_accepted=None)
    )


async def ensure_inbox_for_user(
    db: AsyncSession, game_id: int, user_id: str
) -> None:
    """Bot path: new session on an already-unrecognized game."""
    stmt = (
        pg_insert(UserGamePreference)
        .values(user_id=user_id, game_id=game_id, is_accepted=False)
        .on_conflict_do_nothing(index_elements=["user_id", "game_id"])
    )
    await db.execute(stmt)


async def sync_review_preferences(
    db: AsyncSession,
    game_id: int,
    *,
    previous_status: EnrichmentStatus,
    new_status: EnrichmentStatus,
) -> None:
    if new_status == EnrichmentStatus.NEEDS_REVIEW:
        await mark_needs_review_inbox(db, game_id)
    elif (
        new_status == EnrichmentStatus.ENRICHED
        and previous_status == EnrichmentStatus.NEEDS_REVIEW
    ):
        await clear_review_on_enriched(db, game_id)