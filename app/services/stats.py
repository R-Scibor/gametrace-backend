from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Integer, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import Game, UserGamePreference
from app.models.session import GameSession, SessionStatus
from app.models.user import User
from app.schemas.stats import (
    GameStatEntry,
    HeatmapCell,
    HeatmapResponse,
    PendingErrorEntry,
    StatsSummaryResponse,
)


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


async def heatmap_for_user(
    db: AsyncSession, user: User, days: int
) -> HeatmapResponse:
    """
    7x24 grid of seconds played, bucketed by day-of-week × hour-of-day in
    user's timezone. Includes ONGOING sessions (now() - start_time as
    duration). Excludes soft-deleted, ERROR sessions, and is_ignored games.

    v1 simplification: each session is bucketed by its start_time's local
    DOW/hour — not split across hour/day boundaries. Adequate for visualization;
    revisit if users complain about long sessions appearing in only one cell.
    """
    now_utc = datetime.now(timezone.utc)
    window_start = now_utc - timedelta(days=days)

    # Validate the user's stored tz string; fall back to UTC if invalid.
    try:
        ZoneInfo(user.timezone)
        tz_name = user.timezone
    except (ZoneInfoNotFoundError, ValueError):
        tz_name = "UTC"

    local_start = func.timezone(tz_name, GameSession.start_time)
    pg_dow = func.extract("dow", local_start).cast(Integer).label("pg_dow")
    hour_col = func.extract("hour", local_start).cast(Integer).label("hour")
    duration = func.coalesce(
        GameSession.duration_seconds,
        func.extract("epoch", func.now() - GameSession.start_time),
    ).cast(Integer)

    stmt = (
        select(
            pg_dow,
            hour_col,
            func.sum(duration).label("total_seconds"),
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
            GameSession.status != SessionStatus.ERROR,
            GameSession.deleted_at.is_(None),
            GameSession.start_time >= window_start,
            or_(
                UserGamePreference.is_ignored.is_(None),
                UserGamePreference.is_ignored == False,  # noqa: E712
            ),
        )
        .group_by(pg_dow, hour_col)
    )
    rows = (await db.execute(stmt)).all()

    # Postgres dow: 0=Sun..6=Sat; spec dow: 0=Mon..6=Sun → (pg + 6) % 7
    bucket: dict[tuple[int, int], int] = {}
    for row in rows:
        spec_dow = (row.pg_dow + 6) % 7
        bucket[(spec_dow, row.hour)] = int(row.total_seconds or 0)

    cells = [
        HeatmapCell(dow=d, hour=h, seconds=bucket.get((d, h), 0))
        for d in range(7)
        for h in range(24)
    ]

    return HeatmapResponse(days=days, cells=cells)
