from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import Game, UserGamePreference
from app.models.session import GameSession, SessionStatus
from app.models.user import User
from app.schemas.stats import GameStatEntry, PendingErrorEntry, StatsSummaryResponse


async def summary_for_user(
    db: AsyncSession, user: User, days: int
) -> StatsSummaryResponse:
    """
    Per-user stats summary over the last `days` days.

    Shared by GET /stats/summary and the weekly-report Celery task so the
    push notification content never drifts from what the Dashboard shows.
    Excludes: soft-deleted sessions, ERROR sessions, is_ignored games.
    """
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=days)

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
    per_game_rows = (await db.execute(per_game_stmt)).all()

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
    pending_errors = [
        PendingErrorEntry(
            id=session.id,
            game_id=session.game_id,
            game_name=game_name,
            start_time=session.start_time,
            notes=session.notes,
        )
        for session, game_name in (await db.execute(errors_stmt)).all()
    ]

    return StatsSummaryResponse(
        days=days,
        window_start=window_start,
        window_end=now,
        total_seconds=total_seconds,
        per_game=per_game,
        pending_errors=pending_errors,
    )
