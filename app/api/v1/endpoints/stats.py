from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.game import Game, UserGamePreference
from app.models.session import GameSession, SessionStatus
from app.models.user import User
from app.schemas.stats import (
    ActiveSessionBrief,
    DashboardResponse,
    HeatmapResponse,
    PendingErrorEntry,
    StatsSummaryResponse,
    StreakResponse,
)
from app.services.stats import heatmap_for_user, streak_for_user, summary_for_user

router = APIRouter()


@router.get("/summary", response_model=StatsSummaryResponse)
async def get_stats_summary(
    days: int = Query(default=7, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await summary_for_user(db, user, days)


@router.get("/heatmap", response_model=HeatmapResponse)
async def get_heatmap(
    days: int = Query(default=90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await heatmap_for_user(db, user, days)


@router.get("/streak", response_model=StreakResponse)
async def get_streak(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await streak_for_user(db, user)


def _total_seconds_for_window(rows: list, window_start: datetime) -> int:
    return sum(row.total_seconds for row in rows if row.window_start >= window_start)


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Polling tile endpoint for the Dashboard tab — small fixed payload (two
    totals, the active ONGOING session if any, error banner). Sibling to
    /stats/summary, which returns the user-selectable per-game breakdown.
    """
    now = datetime.now(timezone.utc)
    window_30d = now - timedelta(days=30)
    window_7d = now - timedelta(days=7)

    # "Today" is wall-clock midnight in the user's timezone — unlike the rolling
    # 7d/30d windows, local-vs-UTC drift matters here. Fall back to UTC if the
    # stored tz string is unrecognized (zoneinfo raises on invalid IANA names).
    try:
        user_tz = ZoneInfo(user.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        user_tz = timezone.utc
    local_midnight = datetime.now(user_tz).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    window_today = local_midnight.astimezone(timezone.utc)

    # Compute totals for 30-day window (superset), then filter for 7-day in Python.
    # LEFT JOIN on user_game_preferences so games without a pref row are kept;
    # games with is_ignored=true are excluded (matches /stats/summary behaviour).
    totals_stmt = (
        select(
            GameSession.start_time.label("window_start"),
            func.coalesce(GameSession.duration_seconds, 0).label("total_seconds"),
        )
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
            GameSession.start_time >= window_30d,
            or_(
                UserGamePreference.is_ignored.is_(None),
                UserGamePreference.is_ignored == False,  # noqa: E712
            ),
        )
    )
    totals_result = await db.execute(totals_stmt)
    rows = totals_result.all()

    total_seconds_30d = sum(r.total_seconds for r in rows)
    total_seconds_7d = sum(r.total_seconds for r in rows if r.window_start >= window_7d)
    total_seconds_today = sum(
        r.total_seconds for r in rows if r.window_start >= window_today
    )

    # Active session (ONGOING, not soft-deleted)
    active_stmt = (
        select(GameSession, Game.primary_name, Game.cover_image_url)
        .join(Game, GameSession.game_id == Game.id)
        .where(
            GameSession.user_id == user.discord_id,
            GameSession.status == SessionStatus.ONGOING,
            GameSession.deleted_at.is_(None),
        )
        .order_by(GameSession.start_time.desc())
        .limit(1)
    )
    active_result = await db.execute(active_stmt)
    active_row = active_result.first()
    active_session = (
        ActiveSessionBrief(
            id=active_row[0].id,
            game_id=active_row[0].game_id,
            game_name=active_row[1],
            cover_image_url=active_row[2],
            start_time=active_row[0].start_time,
        )
        if active_row
        else None
    )

    # Pending errors
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

    return DashboardResponse(
        total_seconds_today=total_seconds_today,
        total_seconds_7d=total_seconds_7d,
        total_seconds_30d=total_seconds_30d,
        active_session=active_session,
        pending_errors=pending_errors,
    )
