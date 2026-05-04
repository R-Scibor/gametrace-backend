from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Date, Integer, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import Game, UserGamePreference
from app.models.session import GameSession, SessionStatus
from app.models.user import User
from app.schemas.stats import (
    CompaniesResponse,
    CompanyEntry,
    CompanyRole,
    GameStatEntry,
    GenreEntry,
    GenresResponse,
    HeatmapCell,
    HeatmapResponse,
    PendingErrorEntry,
    StatsSummaryResponse,
    StreakResponse,
    ThemeEntry,
    ThemesResponse,
    WeeklyTrendEntry,
    WeeklyTrendResponse,
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


def _compute_streaks(play_dates: set[date], today: date) -> tuple[int, int]:
    """Pure function — returns (current_streak, longest_streak).

    longest_streak: longest run of consecutive calendar dates in `play_dates`.
    current_streak: counted backwards from today; if today isn't a play day
    but yesterday is, the streak is anchored at yesterday (one-day grace
    so a user looking before they've played today still sees their streak).
    """
    if not play_dates:
        return (0, 0)

    sorted_dates = sorted(play_dates)
    longest = 1
    run = 1
    for prev, curr in zip(sorted_dates, sorted_dates[1:]):
        if (curr - prev).days == 1:
            run += 1
        else:
            run = 1
        if run > longest:
            longest = run

    if today in play_dates:
        anchor = today
    elif (today - timedelta(days=1)) in play_dates:
        anchor = today - timedelta(days=1)
    else:
        return (0, longest)

    current = 0
    cursor = anchor
    while cursor in play_dates:
        current += 1
        cursor -= timedelta(days=1)

    return (current, longest)


async def streak_for_user(db: AsyncSession, user: User) -> StreakResponse:
    """
    Current and longest play-day streak in the user's timezone.

    A "play day" = any calendar date (in user's tz) on which the user has
    at least one non-error, non-deleted session for a non-ignored game.
    No time window — longest_streak needs full history. v1 simplification:
    a session is attributed to the local date of its start_time only.
    """
    try:
        ZoneInfo(user.timezone)
        tz_name = user.timezone
    except (ZoneInfoNotFoundError, ValueError):
        tz_name = "UTC"

    local_date = func.timezone(tz_name, GameSession.start_time).cast(Date)

    stmt = (
        select(local_date.label("d"))
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
            or_(
                UserGamePreference.is_ignored.is_(None),
                UserGamePreference.is_ignored == False,  # noqa: E712
            ),
        )
        .distinct()
    )
    rows = (await db.execute(stmt)).all()
    play_dates: set[date] = {row.d for row in rows}

    today_local = datetime.now(ZoneInfo(tz_name)).date()
    current, longest = _compute_streaks(play_dates, today_local)
    return StreakResponse(current_streak=current, longest_streak=longest)


async def weekly_trend_for_user(
    db: AsyncSession, user: User, weeks: int
) -> WeeklyTrendResponse:
    """
    Total seconds played per ISO week (Monday-start) in the user's timezone,
    oldest first, zero-filled to exactly `weeks` entries. Includes ONGOING
    sessions (now() - start_time as duration). Excludes soft-deleted, ERROR
    sessions, and is_ignored games.
    """
    try:
        ZoneInfo(user.timezone)
        tz_name = user.timezone
    except (ZoneInfoNotFoundError, ValueError):
        tz_name = "UTC"

    tz = ZoneInfo(tz_name)
    today_local = datetime.now(tz).date()
    monday_this_week = today_local - timedelta(days=today_local.weekday())
    oldest_monday = monday_this_week - timedelta(weeks=weeks - 1)
    # Aware datetime at local midnight on oldest_monday — comparable to
    # GameSession.start_time (DateTime(timezone=True)). Lets Postgres use the
    # (user_id, start_time) btree index for the lower bound.
    oldest_monday_utc = datetime.combine(oldest_monday, time.min, tzinfo=tz)

    local_start = func.timezone(tz_name, GameSession.start_time)
    week_start_col = func.date_trunc("week", local_start).cast(Date).label("week_start")
    duration = func.coalesce(
        GameSession.duration_seconds,
        func.extract("epoch", func.now() - GameSession.start_time),
    ).cast(Integer)

    stmt = (
        select(
            week_start_col,
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
            GameSession.start_time >= oldest_monday_utc,
            or_(
                UserGamePreference.is_ignored.is_(None),
                UserGamePreference.is_ignored == False,  # noqa: E712
            ),
        )
        .group_by(week_start_col)
    )
    rows = (await db.execute(stmt)).all()
    bucket: dict[date, int] = {row.week_start: int(row.total_seconds or 0) for row in rows}

    entries = [
        WeeklyTrendEntry(
            week_start=(monday := oldest_monday + timedelta(weeks=i)),
            total_seconds=bucket.get(monday, 0),
        )
        for i in range(weeks)
    ]
    return WeeklyTrendResponse(weeks=entries)


async def _jsonb_breakdown(
    db: AsyncSession, user: User, jsonb_col
) -> list[tuple[str, int]]:
    """Aggregate session duration grouped by JSONB-array element from games.<col>.

    Excludes ERROR/deleted/is_ignored. ONGOING sessions counted via
    coalesce(duration_seconds, extract('epoch', now() - start_time)).

    Returns list of (tag, total_seconds) sorted by total_seconds desc.

    Postgres treats jsonb_array_elements_text in the SELECT list as an
    implicit lateral join — empty arrays produce zero rows, so empty-tag
    games drop out automatically.
    """
    tag_col = func.jsonb_array_elements_text(jsonb_col).label("tag")
    duration = func.coalesce(
        GameSession.duration_seconds,
        func.extract("epoch", func.now() - GameSession.start_time),
    ).cast(Integer)
    total_col = func.sum(duration).label("total_seconds")

    stmt = (
        select(tag_col, total_col)
        .select_from(GameSession)
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
            GameSession.status != SessionStatus.ERROR,
            GameSession.deleted_at.is_(None),
            or_(
                UserGamePreference.is_ignored.is_(None),
                UserGamePreference.is_ignored == False,  # noqa: E712
            ),
        )
        .group_by(tag_col)
        .order_by(total_col.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [(row.tag, int(row.total_seconds or 0)) for row in rows]


async def genres_for_user(db: AsyncSession, user: User) -> GenresResponse:
    rows = await _jsonb_breakdown(db, user, Game.genres)
    return GenresResponse(
        items=[GenreEntry(genre=t, total_seconds=s) for t, s in rows]
    )


async def themes_for_user(db: AsyncSession, user: User) -> ThemesResponse:
    rows = await _jsonb_breakdown(db, user, Game.themes)
    return ThemesResponse(
        items=[ThemeEntry(theme=t, total_seconds=s) for t, s in rows]
    )


async def companies_for_user(
    db: AsyncSession, user: User, role: CompanyRole, limit: int
) -> CompaniesResponse:
    """Top companies by total seconds played for the given role.

    role chooses Game.developers vs Game.publishers JSONB column.
    Same exclusion filter as other stats endpoints. Ties broken by name asc.
    """
    jsonb_col = (
        Game.developers if role == CompanyRole.developer else Game.publishers
    )

    name_col = func.jsonb_array_elements_text(jsonb_col).label("name")
    duration = func.coalesce(
        GameSession.duration_seconds,
        func.extract("epoch", func.now() - GameSession.start_time),
    ).cast(Integer)
    total_col = func.sum(duration).label("total_seconds")
    game_count_col = func.count(func.distinct(GameSession.game_id)).label(
        "game_count"
    )

    stmt = (
        select(name_col, total_col, game_count_col)
        .select_from(GameSession)
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
            GameSession.status != SessionStatus.ERROR,
            GameSession.deleted_at.is_(None),
            or_(
                UserGamePreference.is_ignored.is_(None),
                UserGamePreference.is_ignored == False,  # noqa: E712
            ),
        )
        .group_by(name_col)
        .order_by(total_col.desc(), name_col.asc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    return CompaniesResponse(
        items=[
            CompanyEntry(
                name=r.name,
                total_seconds=int(r.total_seconds or 0),
                game_count=int(r.game_count or 0),
            )
            for r in rows
        ]
    )
