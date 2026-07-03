import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import require_admin
from app.core.database import get_db
from app.core.observability import log_admin_action
from app.models.game import Game, GameAlias, UserGamePreference
from app.models.session import GameSession
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# POST /games/{game_id}/merge/{target_id}
# ---------------------------------------------------------------------------

@router.post("/games/{game_id}/merge/{target_id}", status_code=204)
async def merge_game(
    game_id: int,
    target_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),  # auth required
):
    """
    Merge game_id into target_id (ACID transaction):
    1. Reassign all game_aliases → target_id
    2. Reassign all game_sessions → target_id
    3. Merge user_game_preferences (drop conflicts, reassign rest)
    4. Delete source game record

    Returns 204 No Content on success.
    Returns 404 if either game does not exist.
    Returns 400 if game_id == target_id.
    """
    if game_id == target_id:
        raise HTTPException(status_code=400, detail="Cannot merge a game into itself.")

    source = await db.get(Game, game_id)
    target = await db.get(Game, target_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found.")
    if target is None:
        raise HTTPException(status_code=404, detail=f"Game {target_id} not found.")

    # ── All operations in a single transaction ─────────────────────────────
    # 1. Reassign aliases (unique on discord_process_name — no conflicts possible)
    await db.execute(
        update(GameAlias).where(GameAlias.game_id == game_id).values(game_id=target_id)
    )

    # 2. Reassign sessions
    await db.execute(
        update(GameSession).where(GameSession.game_id == game_id).values(game_id=target_id)
    )

    # 3. UserGamePreference has UNIQUE (user_id, game_id).
    #    Drop source rows where target already has a preference for the same user.
    await db.execute(
        delete(UserGamePreference).where(
            UserGamePreference.game_id == game_id,
            UserGamePreference.user_id.in_(
                select(UserGamePreference.user_id).where(
                    UserGamePreference.game_id == target_id
                )
            ),
        )
    )
    await db.execute(
        update(UserGamePreference)
        .where(UserGamePreference.game_id == game_id)
        .values(game_id=target_id)
    )

    # 4. Delete the source game record
    await db.delete(source)
    await db.commit()

    logger.info("merge_game: game_id=%d merged into target_id=%d", game_id, target_id)
    log_admin_action(
        user.discord_id, "merge_game", f"game:{game_id}", after=f"target:{target_id}"
    )
