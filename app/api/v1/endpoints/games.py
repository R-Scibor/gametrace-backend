import base64
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.game import CoverSource, EnrichmentStatus, Game, GameAlias, UserGamePreference
from app.models.session import GameSession
from app.models.user import User
from app.schemas.game import CoverUpload, GameResponse
from app.schemas.session import SessionResponse

router = APIRouter()
logger = logging.getLogger(__name__)

COVERS_DIR = "/app/covers"
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


# ---------------------------------------------------------------------------
# GET /games
# ---------------------------------------------------------------------------

@router.get("", response_model=list[GameResponse])
async def list_games(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    status: EnrichmentStatus | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Return games that the current user has at least one session for.
    Excludes games the user has marked as ignored.
    Optional ?status= filter (e.g. NEEDS_REVIEW for the Unrecognized tab).
    """
    # Sub-query: game IDs the user has ignored
    ignored_sq = (
        select(UserGamePreference.game_id)
        .where(
            UserGamePreference.user_id == user.discord_id,
            UserGamePreference.is_ignored.is_(True),
        )
        .scalar_subquery()
    )

    query = (
        select(Game)
        .join(GameSession, GameSession.game_id == Game.id)
        .where(
            GameSession.user_id == user.discord_id,
            GameSession.deleted_at.is_(None),
            Game.id.not_in(ignored_sq),
        )
        .distinct()
        .order_by(Game.primary_name)
        .offset(skip)
        .limit(limit)
    )

    if status is not None:
        query = query.where(Game.enrichment_status == status)

    result = await db.execute(query)
    return result.scalars().all()


# ---------------------------------------------------------------------------
# GET /{game_id}/sessions  (kept here — same router)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# POST /{game_id}/merge/{target_id}
# ---------------------------------------------------------------------------

@router.post("/{game_id}/merge/{target_id}", status_code=204)
async def merge_game(
    game_id: int,
    target_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),  # auth required
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


# ---------------------------------------------------------------------------
# PUT /{game_id}/cover
# ---------------------------------------------------------------------------

@router.put("/{game_id}/cover", response_model=GameResponse)
async def upload_cover(
    game_id: int,
    body: CoverUpload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Upload a custom cover image (Base64-encoded).
    Saves to the covers Docker volume, sets cover_source=CUSTOM.
    The Celery enrichment worker will NOT overwrite this cover on future enrichments.
    """
    game = await db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found.")

    ext = body.extension.lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported extension '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    try:
        image_data = base64.b64decode(body.image_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 encoding.")

    os.makedirs(COVERS_DIR, exist_ok=True)
    filename = f"{game_id}.{ext}"
    filepath = os.path.join(COVERS_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(image_data)

    # Build an absolute URL the client can use
    base_url = str(request.base_url).rstrip("/")
    cover_url = f"{base_url}/covers/{filename}"

    game.cover_image_url = cover_url
    game.cover_source = CoverSource.CUSTOM
    await db.commit()
    await db.refresh(game)

    logger.info("upload_cover: game_id=%d cover saved as %s", game_id, filepath)
    return game
