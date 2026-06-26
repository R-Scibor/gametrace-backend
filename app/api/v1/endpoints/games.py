import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.game import EnrichmentStatus, Game, GameAlias, UserGamePreference
from app.models.session import GameSession
from app.models.user import User
from app.schemas.game import (
    CoverUpload,
    GameListResponse,
    GameResolveOut,
    GameResponse,
    GameSuggestItem,
    GameSuggestResponse,
)
from app.services.game_matching import _confidence
from app.schemas.session import SessionResponse
from app.schemas.stats import GameStatsResponse
from app.services.library_visibility import (
    ignored_only_filter,
    library_excluded_filter,
    library_visible_filter,
    review_inbox_filter,
)
from app.services.session_visibility import visible_session
from app.services.stats import game_stats_for_user

router = APIRouter()
logger = logging.getLogger(__name__)


def _game_response(game: Game, pref: UserGamePreference | None) -> GameResponse:
    return GameResponse(
        id=game.id,
        primary_name=game.primary_name,
        cover_image_url=game.cover_image_url,
        cover_source=game.cover_source,
        enrichment_status=game.enrichment_status,
        is_ignored=pref.is_ignored if pref else False,
        is_accepted=pref.is_accepted if pref else None,
    )


# ---------------------------------------------------------------------------
# GET /games
# ---------------------------------------------------------------------------

@router.get("", response_model=GameListResponse)
async def list_games(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    status: EnrichmentStatus | None = Query(default=None),
    is_ignored: bool | None = Query(default=None),
    in_library: bool | None = Query(default=None),
    q: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Return games that the current user has at least one session for.
    Main library excludes ignored games and unaccepted NEEDS_REVIEW stubs.
    Optional ?status=NEEDS_REVIEW for the Unrecognized inbox tab.
    Optional ?is_ignored=true for the hidden-games tab.
    Optional ?in_library=false for the out-of-library tab (ignored + unaccepted NEEDS_REVIEW).
    Optional ?q= for server-side name search (case-insensitive substring match).
    """
    pref_join = and_(
        UserGamePreference.game_id == Game.id,
        UserGamePreference.user_id == user.discord_id,
    )

    base_filters = [
        GameSession.user_id == user.discord_id,
        *visible_session(),
    ]
    if q:
        base_filters.append(Game.primary_name.ilike(f"%{q}%"))

    if is_ignored is True:
        visibility_filter = ignored_only_filter()
        if status is not None:
            base_filters.append(Game.enrichment_status == status)
    elif in_library is False:
        visibility_filter = library_excluded_filter()
        if status is not None:
            base_filters.append(Game.enrichment_status == status)
    elif status == EnrichmentStatus.NEEDS_REVIEW:
        base_filters.append(Game.enrichment_status == EnrichmentStatus.NEEDS_REVIEW)
        visibility_filter = review_inbox_filter()
    else:
        if status is not None:
            base_filters.append(Game.enrichment_status == status)
        visibility_filter = library_visible_filter()

    count_q = (
        select(func.count(func.distinct(Game.id)))
        .join(GameSession, GameSession.game_id == Game.id)
        .outerjoin(UserGamePreference, pref_join)
        .where(*base_filters, visibility_filter)
    )
    total = (await db.execute(count_q)).scalar_one()

    items_q = (
        select(Game)
        .join(GameSession, GameSession.game_id == Game.id)
        .outerjoin(UserGamePreference, pref_join)
        .where(*base_filters, visibility_filter)
        .distinct()
        .order_by(Game.primary_name)
        .offset(skip)
        .limit(limit)
    )
    games = (await db.execute(items_q)).scalars().all()

    pref_map: dict[int, UserGamePreference] = {}
    if games:
        prefs = (
            await db.execute(
                select(UserGamePreference).where(
                    UserGamePreference.user_id == user.discord_id,
                    UserGamePreference.game_id.in_([g.id for g in games]),
                )
            )
        ).scalars().all()
        pref_map = {p.game_id: p for p in prefs}

    return GameListResponse(
        total=total,
        items=[_game_response(g, pref_map.get(g.id)) for g in games],
    )


# ---------------------------------------------------------------------------
# GET /resolve
# ---------------------------------------------------------------------------

@router.get("/resolve", response_model=GameResolveOut | None)
async def resolve_game(
    name: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Map a free-text game name to a Game record from the calling user's library.

    Match strategy (exact, case-insensitive):
      1. games.primary_name == name
      2. game_aliases.discord_process_name == name

    Scope: games the user has at least one non-soft-deleted session for.
      - ERROR sessions count (the user played the game).
      - is_ignored games still resolve (ignore is a display filter).

    Returns 200 with the matched game, or 200 with body `null` on miss.
    """
    needle = name.strip().lower()

    user_games_sq = (
        select(GameSession.game_id)
        .where(
            GameSession.user_id == user.discord_id,
            *visible_session(),
        )
        .distinct()
        .scalar_subquery()
    )

    query = (
        select(Game.id, Game.primary_name)
        .outerjoin(GameAlias, GameAlias.game_id == Game.id)
        .where(
            Game.id.in_(user_games_sq),
            or_(
                func.lower(Game.primary_name) == needle,
                func.lower(GameAlias.discord_process_name) == needle,
            ),
        )
        .limit(1)
    )

    row = (await db.execute(query)).first()
    if row is None:
        return None
    return GameResolveOut(game_id=row.id, name=row.primary_name)


# ---------------------------------------------------------------------------
# GET /suggest
# ---------------------------------------------------------------------------

@router.get("/suggest", response_model=GameSuggestResponse)
async def suggest_games(
    q: str = Query(..., min_length=1),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Fuzzy-search the global games catalog by name.

    Scope: ALL games (not restricted to the caller's library).
    Prefilters with ILIKE-any-token over primary_name and aliases, then
    scores each candidate with _confidence() (max over primary_name + all
    aliases for that game). Drops score < 0.3, sorts descending, paginates.
    """
    tokens = q.split()

    # Build ILIKE prefilter: any token matches primary_name or any alias
    ilike_conditions = []
    for token in tokens:
        ilike_conditions.append(Game.primary_name.ilike(f"%{token}%"))
        ilike_conditions.append(GameAlias.discord_process_name.ilike(f"%{token}%"))

    # Step 1: get distinct game IDs that survive the prefilter
    id_query = (
        select(Game.id)
        .outerjoin(GameAlias, GameAlias.game_id == Game.id)
        .where(or_(*ilike_conditions))
        .distinct()
    )
    matched_ids = (await db.execute(id_query)).scalars().all()

    if not matched_ids:
        return GameSuggestResponse(total=0, items=[])

    # Step 2: load full rows + aliases for matched games
    games_query = (
        select(Game)
        .where(Game.id.in_(matched_ids))
        .options(selectinload(Game.aliases))
    )
    games = (await db.execute(games_query)).scalars().all()

    # Step 3: score, apply noise floor, sort
    scored: list[tuple[Game, float]] = []
    for game in games:
        alias_names = [a.discord_process_name for a in game.aliases]
        score = max(
            [_confidence(q, game.primary_name)]
            + [_confidence(q, alias) for alias in alias_names]
        )
        if score >= 0.3:
            scored.append((game, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    total = len(scored)
    page = scored[skip : skip + limit]

    return GameSuggestResponse(
        total=total,
        items=[
            GameSuggestItem(
                game_id=g.id,
                primary_name=g.primary_name,
                cover_image_url=g.cover_image_url,
                enrichment_status=g.enrichment_status,
                score=score,
            )
            for g, score in page
        ],
    )


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
    result = await db.execute(
        select(GameSession)
        .options(selectinload(GameSession.game))
        .where(
            GameSession.user_id == user.discord_id,
            GameSession.game_id == game_id,
            *visible_session(),
        )
        .order_by(GameSession.start_time.desc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


# ---------------------------------------------------------------------------
# GET /{game_id}/stats
# ---------------------------------------------------------------------------

@router.get("/{game_id}/stats", response_model=GameStatsResponse)
async def get_game_stats(
    game_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Lifetime playtime stats for a single game in the caller's library:
    total_seconds (ONGOING counted live), session_count, first/last played.
    404 when the caller has no visible sessions for the game.
    """
    stats = await game_stats_for_user(db, user, game_id)
    if stats is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return stats


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
    user: User = Depends(get_current_user),
):
    """
    Disabled. Custom cover uploads previously mutated the global Game row
    (cover_source=CUSTOM) with no per-user scoping or RBAC, so one user could
    overwrite the shared cover art seen by everyone — and a stale on-disk URL
    could leave a game with broken, unrecoverable art. The write path is closed
    pending admin-only controls; see docs/roadmap.md → "Game covers". The storage
    machinery (CUSTOM enum, /covers static mount, enrichment skip-guard) is
    intentionally retained for that future feature.
    """
    raise HTTPException(
        status_code=403,
        detail="Custom cover uploads are temporarily disabled pending admin controls.",
    )
