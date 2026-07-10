"""Admin catalog operations."""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import require_admin
from app.core.database import get_db
from app.core.observability import log_admin_action
from app.models.game import CoverSource, EnrichmentStatus, Game, GameAlias
from app.models.session import GameSession
from app.models.user import User
from app.schemas.admin import AdminGameItem, AdminGameListResponse, IgdbLinkRequest
from app.schemas.game import GameMatchRequest, GameResponse, IGDBCandidateOut
from app.services.enrichment_dispatch import queue_enrichment
from app.services.game_matching import (
    _igdb_fetch_by_id,
    _igdb_search_candidates,
    _RateLimited,
    apply_igdb_metadata,
)
from app.services.session_visibility import visible_session

router = APIRouter()
logger = logging.getLogger(__name__)

session_count_expr = func.count(func.distinct(GameSession.id))
aliases_expr = func.array_remove(
    func.array_agg(
        aggregate_order_by(GameAlias.discord_process_name, GameAlias.discord_process_name.asc())
    ),
    None,
)


# ---------------------------------------------------------------------------
# GET /games
# ---------------------------------------------------------------------------

@router.get("/games", response_model=AdminGameListResponse)
async def list_games(
    status: EnrichmentStatus = Query(EnrichmentStatus.NEEDS_REVIEW),
    q: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    sort: Literal["sessions_desc", "id_asc", "name_asc"] = "sessions_desc",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),  # admin gate (router-level too; explicit for clarity)
):
    """Global catalog list for admin review: filterable, searchable, paginated."""
    filters = [Game.enrichment_status == status]

    q_stripped = q.strip() if q else None
    if q_stripped:
        matching_ids = (
            select(Game.id)
            .outerjoin(GameAlias, GameAlias.game_id == Game.id)
            .where(
                or_(
                    Game.primary_name.ilike(f"%{q_stripped}%"),
                    GameAlias.discord_process_name.ilike(f"%{q_stripped}%"),
                )
            )
            .distinct()
        )
        filters.append(Game.id.in_(matching_ids))

    total = await db.scalar(select(func.count()).select_from(Game).where(*filters))

    sort_map = {
        "sessions_desc": (session_count_expr.desc(), Game.id.asc()),
        "id_asc": (Game.id.asc(),),
        "name_asc": (Game.primary_name.asc(),),
    }

    result = await db.execute(
        select(
            Game.id,
            Game.primary_name,
            Game.enrichment_status,
            Game.cover_image_url,
            Game.cover_source,
            Game.external_api_id,
            session_count_expr.label("session_count"),
            aliases_expr.label("aliases"),
        )
        .outerjoin(
            GameSession,
            and_(GameSession.game_id == Game.id, *visible_session()),
        )
        .outerjoin(GameAlias, GameAlias.game_id == Game.id)
        .where(*filters)
        .group_by(Game.id)
        .order_by(*sort_map[sort])
        .offset(skip)
        .limit(limit)
    )

    items = [
        AdminGameItem(
            id=row.id,
            primary_name=row.primary_name,
            enrichment_status=row.enrichment_status,
            cover_image_url=row.cover_image_url,
            cover_source=row.cover_source,
            external_api_id=row.external_api_id,
            aliases=row.aliases or [],
            session_count=row.session_count,
        )
        for row in result.all()
    ]

    return AdminGameListResponse(total=total, items=items)


# ---------------------------------------------------------------------------
# POST /games/{game_id}/enrich
# ---------------------------------------------------------------------------

@router.post("/games/{game_id}/enrich", status_code=202)
async def requeue_enrichment(
    game_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Re-queue the Celery enrichment task for an existing game (no row reset)."""
    game = await db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found.")

    queue_enrichment(game_id)

    log_admin_action(user.discord_id, "enrich_requeue", f"game:{game_id}")

    return {"queued": True}


# ---------------------------------------------------------------------------
# POST /games/match
# ---------------------------------------------------------------------------

@router.post("/games/match", response_model=list[IGDBCandidateOut])
async def match_game(
    body: GameMatchRequest,
    user: User = Depends(require_admin),
):
    """
    Search IGDB synchronously and return ranked candidates for *body.query*.

    Admin mirror of POST /games/match — read-only, no DB write, no audit log.
    """
    try:
        candidates = await asyncio.to_thread(_igdb_search_candidates, body.query)
    except _RateLimited:
        raise HTTPException(
            status_code=503,
            detail="Game database temporarily unavailable",
        )
    except Exception:
        logger.exception("match_game: IGDB error for query=%r", body.query)
        raise HTTPException(
            status_code=502,
            detail="Upstream game database error",
        )

    return [
        IGDBCandidateOut(
            igdb_id=c.igdb_id,
            name=c.name,
            year=c.year,
            cover_url=c.cover_url,
            score=c.score,
        )
        for c in candidates
    ]


# ---------------------------------------------------------------------------
# POST /games/{game_id}/igdb-link
# ---------------------------------------------------------------------------

def _igdb_link_audit_snapshot(game: Game) -> str:
    return (
        f"primary_name={game.primary_name!r} "
        f"enrichment_status={EnrichmentStatus(game.enrichment_status).value} "
        f"external_api_id={game.external_api_id!r} "
        f"cover_source={CoverSource(game.cover_source).value}"
    )


@router.post("/games/{game_id}/igdb-link", response_model=GameResponse)
async def igdb_link_game(
    game_id: int,
    body: IgdbLinkRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Link an existing catalog row to a chosen IGDB game and apply its metadata."""
    game = await db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found.")

    other = await db.scalar(
        select(Game.id).where(
            Game.external_api_id == str(body.igdb_id),
            Game.id != game_id,
        )
    )
    if other is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "IGDB id already linked to another game",
                "conflicting_game_id": other,
            },
        )

    try:
        fetched = await asyncio.to_thread(_igdb_fetch_by_id, body.igdb_id)
    except _RateLimited:
        raise HTTPException(
            status_code=503,
            detail="Game database temporarily unavailable",
        )

    if fetched is None:
        raise HTTPException(status_code=404, detail="IGDB game not found")

    canonical_name, meta = fetched

    before = _igdb_link_audit_snapshot(game)
    await apply_igdb_metadata(
        db,
        game,
        canonical_name,
        meta,
        igdb_id=body.igdb_id,
    )
    await db.commit()
    await db.refresh(game)

    log_admin_action(
        user.discord_id,
        "igdb_link",
        f"game:{game_id}",
        before=before,
        after=_igdb_link_audit_snapshot(game),
    )

    return game