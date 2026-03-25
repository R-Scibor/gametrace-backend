from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.game import Game, UserGamePreference
from app.models.session import GameSession, SessionStatus
from app.models.user import User
from app.schemas.stats import GameStatEntry, PendingErrorEntry, StatsSummaryResponse

router = APIRouter()


@router.get("/summary", response_model=StatsSummaryResponse)
async def get_stats_summary(
    days: int = Query(default=7, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=days)

    # Per-game totals: COMPLETED sessions in window, not soft-deleted, game not ignored
    per_game_stmt = (
        select(
            GameSession.game_id,
            Game.primary_name,
            Game.cover_image_url,
            func.sum(func.coalesce(GameSession.duration_seconds, 0)).label("total_seconds"),
        )
        .join(Game, GameSession.game_id == Game.id)
        .outerjoin(
            UserGamePreference,
            and_(
                UserGamePreference.game_id == GameSession.game_id,
                UserGamePreference.user_id == user.discord_id,
            ),
        )
        .where(
            GameSession.user_id == user.discord_id,
            GameSession.status == SessionStatus.COMPLETED,
            GameSession.deleted_at.is_(None),
            GameSession.start_time >= window_start,
            or_(
                UserGamePreference.is_ignored.is_(None),
                UserGamePreference.is_ignored == False,  # noqa: E712
            ),
        )
        .group_by(GameSession.game_id, Game.primary_name, Game.cover_image_url)
        .order_by(func.sum(func.coalesce(GameSession.duration_seconds, 0)).desc())
    )
    per_game_result = await db.execute(per_game_stmt)
    per_game_rows = per_game_result.all()

    per_game = [
        GameStatEntry(
            game_id=row.game_id,
            game_name=row.primary_name,
            cover_image_url=row.cover_image_url,
            total_seconds=row.total_seconds,
        )
        for row in per_game_rows
    ]
    total_seconds = sum(e.total_seconds for e in per_game)

    # Pending errors: all ERROR sessions for this user, not soft-deleted
    errors_stmt = (
        select(GameSession, Game.primary_name)
        .join(Game, GameSession.game_id == Game.id)
        .where(
            GameSession.user_id == user.discord_id,
            GameSession.status == SessionStatus.ERROR,
            GameSession.deleted_at.is_(None),
        )
        .order_by(GameSession.start_time.desc())
    )
    errors_result = await db.execute(errors_stmt)
    pending_errors = [
        PendingErrorEntry(
            id=session.id,
            game_id=session.game_id,
            game_name=game_name,
            start_time=session.start_time,
            notes=session.notes,
        )
        for session, game_name in errors_result.all()
    ]

    return StatsSummaryResponse(
        days=days,
        window_start=window_start,
        window_end=now,
        total_seconds=total_seconds,
        per_game=per_game,
        pending_errors=pending_errors,
    )
