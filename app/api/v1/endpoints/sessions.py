from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.game import Game
from app.models.session import GameSession, SessionSource, SessionStatus
from app.models.user import User
from app.schemas.session import ConflictResponse, SessionCreate, SessionPatch, SessionResponse

router = APIRouter()


async def _check_overlap(
    db: AsyncSession,
    user_id: str,
    start_time: datetime,
    end_time: datetime,
    exclude_id: int | None = None,
) -> GameSession | None:
    stmt = (
        select(GameSession)
        .options(selectinload(GameSession.game))
        .where(
            GameSession.user_id == user_id,
            GameSession.deleted_at.is_(None),
            GameSession.status.in_(
                [SessionStatus.ONGOING, SessionStatus.COMPLETED]
            ),
            GameSession.start_time < end_time,
            or_(GameSession.end_time.is_(None), GameSession.end_time > start_time),
        )
        .limit(1)
    )
    if exclude_id is not None:
        stmt = stmt.where(GameSession.id != exclude_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    status_filter: list[SessionStatus] | None = Query(default=None, alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    List the caller's sessions across all games — primarily for the Dashboard
    "Recents" tile. Excludes soft-deleted rows. Optional `?status=` filter
    accepts multiple values (e.g. `?status=COMPLETED&status=ERROR`).
    """
    stmt = (
        select(GameSession)
        .options(selectinload(GameSession.game))
        .where(
            GameSession.user_id == user.discord_id,
            GameSession.deleted_at.is_(None),
        )
        .order_by(GameSession.start_time.desc())
        .offset(skip)
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(GameSession.status.in_(status_filter))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(GameSession)
        .options(selectinload(GameSession.game))
        .where(GameSession.id == session_id, GameSession.user_id == user.discord_id)
    )
    session = result.scalar_one_or_none()
    if session is None or session.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ConflictResponse}},
)
async def create_session(
    payload: SessionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Verify game exists
    game = await db.get(Game, payload.game_id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")

    conflict = await _check_overlap(
        db, user.discord_id, payload.start_time, payload.end_time
    )
    if conflict is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "detail": "Session overlaps with an existing session",
                "conflicting_session": SessionResponse.model_validate(conflict).model_dump(mode="json"),
            },
        )

    duration = int((payload.end_time - payload.start_time).total_seconds())
    session = GameSession(
        user_id=user.discord_id,
        game_id=payload.game_id,
        start_time=payload.start_time,
        end_time=payload.end_time,
        duration_seconds=duration,
        status=SessionStatus.COMPLETED,
        source=SessionSource.MANUAL,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    result = await db.execute(
        select(GameSession)
        .options(selectinload(GameSession.game))
        .where(GameSession.id == session.id)
    )
    return result.scalar_one()


@router.patch(
    "/{session_id}",
    response_model=SessionResponse,
    responses={409: {"model": ConflictResponse}},
)
async def patch_session(
    session_id: int,
    payload: SessionPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(GameSession)
        .options(selectinload(GameSession.game))
        .where(GameSession.id == session_id, GameSession.user_id == user.discord_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    if session.status == SessionStatus.ONGOING:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot edit an ONGOING session — managed by bot",
        )

    # Discard: only for ERROR sessions
    if payload.discard:
        if session.status != SessionStatus.ERROR:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only ERROR sessions can be discarded",
            )
        session.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(session)
        return session

    # Update end_time → fixes ERROR or updates COMPLETED
    if payload.end_time is not None:
        if payload.end_time <= session.start_time:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="end_time must be after start_time",
            )
        conflict = await _check_overlap(
            db, user.discord_id, session.start_time, payload.end_time, exclude_id=session_id
        )
        if conflict is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "detail": "Session overlaps with an existing session",
                    "conflicting_session": SessionResponse.model_validate(conflict).model_dump(mode="json"),
                },
            )
        session.end_time = payload.end_time
        session.duration_seconds = int(
            (payload.end_time - session.start_time).total_seconds()
        )
        session.status = SessionStatus.COMPLETED

    await db.commit()
    await db.refresh(session)
    return session


