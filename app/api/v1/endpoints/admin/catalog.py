"""Admin catalog operations."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import require_admin
from app.core.database import get_db
from app.core.observability import log_admin_action
from app.models.game import EnrichmentStatus, Game, GameAlias
from app.models.session import GameSession
from app.models.user import User
from app.schemas.admin import AdminGameItem, AdminGameListResponse
from app.services.enrichment_dispatch import queue_enrichment
from app.services.session_visibility import visible_session

router = APIRouter()

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