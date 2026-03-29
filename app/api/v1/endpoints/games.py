from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.game import UserGamePreference
from app.models.session import GameSession
from app.models.user import User
from app.schemas.session import SessionResponse

router = APIRouter()


@router.get("/{game_id}/sessions", response_model=list[SessionResponse])
async def list_game_sessions(
    game_id: int,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pref_result = await db.execute(
        select(UserGamePreference).where(
            UserGamePreference.user_id == user.discord_id,
            UserGamePreference.game_id == game_id,
        )
    )
    pref = pref_result.scalar_one_or_none()
    if pref is not None and pref.is_ignored:
        return []

    result = await db.execute(
        select(GameSession)
        .options(selectinload(GameSession.game))
        .where(
            GameSession.user_id == user.discord_id,
            GameSession.game_id == game_id,
            GameSession.deleted_at.is_(None),
        )
        .order_by(GameSession.start_time.desc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()
