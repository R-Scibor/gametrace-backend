import asyncio
import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import Integer, and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.api.v1.endpoints.auth import get_current_or_bot_user, get_current_user
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models.game import CoverSource, EnrichmentStatus, Game, GameAlias, UserGamePreference
from app.models.session import GameSession, SessionStatus
from app.models.user import User
from app.services.demo import DEMO_DISCORD_ID
from app.services.game_aliases import add_alias
from app.schemas.game import (
    GameCreateRequest,
    GameListResponse,
    GameMatchRequest,
    GameResolveOut,
    GameResponse,
    GameSuggestItem,
    GameSuggestResponse,
    IGDBCandidateOut,
)
from app.services.game_matching import (
    _confidence,
    _igdb_fetch_by_id,
    _igdb_search_candidates,
    _RateLimited,
)
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


def _playtime_expr():
    """Lifetime seconds for the joined GameSession rows: COMPLETED durations +
    ONGOING counted live, else 0. Shared by the library list and single-game
    fetch so a game's playtime is identical in both."""
    return func.coalesce(
        func.sum(
            case(
                (GameSession.status == SessionStatus.COMPLETED, GameSession.duration_seconds),
                (
                    GameSession.status == SessionStatus.ONGOING,
                    func.extract("epoch", func.now() - GameSession.start_time),
                ),
                else_=0,
            )
        ),
        0,
    ).cast(Integer).label("total_seconds")


def _last_played_expr():
    return func.max(GameSession.start_time).label("last_played")


def _game_response(
    game: Game,
    pref: UserGamePreference | None,
    total_seconds: int = 0,
    last_played=None,
) -> GameResponse:
    return GameResponse(
        id=game.id,
        primary_name=game.primary_name,
        cover_image_url=game.cover_image_url,
        cover_source=game.cover_source,
        enrichment_status=game.enrichment_status,
        is_ignored=pref.is_ignored if pref else False,
        is_accepted=pref.is_accepted if pref else None,
        total_seconds=total_seconds,
        last_played=last_played,
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
    genre: str | None = Query(default=None),
    theme: str | None = Query(default=None),
    developer: str | None = Query(default=None),
    publisher: str | None = Query(default=None),
    release_decade: str | None = Query(default=None, pattern=r"^\d{3}0s$"),
    sort: Literal["name", "playtime", "last_played"] = Query(default="name"),
    order: Literal["asc", "desc"] | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_or_bot_user),
):
    """
    Return games that the current user has at least one session for.
    Main library excludes ignored games and unaccepted NEEDS_REVIEW stubs.
    Optional ?status=NEEDS_REVIEW for the Unrecognized inbox tab.
    Optional ?is_ignored=true for the hidden-games tab.
    Optional ?in_library=false for the out-of-library tab (ignored + unaccepted NEEDS_REVIEW).
    Optional ?q= for server-side name search (case-insensitive substring match).

    Also reachable via the bot-service credential (X-Bot-Service-Secret +
    X-Discord-Id) for the Discord bot's read-only /recent-review-count check —
    see get_current_or_bot_user.
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
    if genre:
        base_filters.append(Game.genres.contains([genre]))
    if theme:
        base_filters.append(Game.themes.contains([theme]))
    if developer:
        base_filters.append(Game.developers.contains([developer]))
    if publisher:
        base_filters.append(Game.publishers.contains([publisher]))
    if release_decade:
        start_year = int(release_decade[:-1])
        if not (1 <= start_year <= 9989):  # keep date() bounds representable
            raise HTTPException(status_code=422, detail="release_decade out of range")
        base_filters.append(Game.first_release_date >= date(start_year, 1, 1))
        base_filters.append(Game.first_release_date < date(start_year + 10, 1, 1))

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

    playtime_expr = _playtime_expr()
    last_played_expr = _last_played_expr()

    descending = (order == "desc") if order is not None else (sort != "name")
    if sort == "playtime":
        sort_col = playtime_expr
    elif sort == "last_played":
        sort_col = last_played_expr
    else:
        sort_col = Game.primary_name
    primary = sort_col.desc() if descending else sort_col.asc()
    if sort == "name":
        order_by = [primary, Game.id.asc()]
    else:
        order_by = [primary, Game.primary_name.asc(), Game.id.asc()]

    count_q = (
        select(func.count(func.distinct(Game.id)))
        .join(GameSession, GameSession.game_id == Game.id)
        .outerjoin(UserGamePreference, pref_join)
        .where(*base_filters, visibility_filter)
    )
    total = (await db.execute(count_q)).scalar_one()

    items_q = (
        select(Game, playtime_expr, last_played_expr)
        .join(GameSession, GameSession.game_id == Game.id)
        .outerjoin(UserGamePreference, pref_join)
        .where(*base_filters, visibility_filter)
        .group_by(Game.id)
        .order_by(*order_by)
        .offset(skip)
        .limit(limit)
    )
    rows = (await db.execute(items_q)).all()
    games = [row[0] for row in rows]

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
        items=[
            _game_response(
                row[0],
                pref_map.get(row[0].id),
                total_seconds=row.total_seconds,
                last_played=row.last_played,
            )
            for row in rows
        ],
    )


# ---------------------------------------------------------------------------
# POST /games  (create or link)
# ---------------------------------------------------------------------------

@router.post("", response_model=GameResponse, status_code=201)
@limiter.limit("60/hour")
async def create_or_link_game(
    request: Request,
    body: GameCreateRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Create a new global Game row or link to an existing one.

    Two modes (exactly one):
    - igdb_id mode: look up IGDB by id, dedupe first (returns 200 if already
      known), else insert an ENRICHED row (returns 201).
    - unrecognized mode: insert a NEEDS_REVIEW stub (returns 201).

    Optional *query* is stored as a GameAlias so future voice/resolve calls
    can map the typed string to the game.

    Rate limited to 60/hour per credential (429 past that). Looser than
    /games/match: igdb_id mode dedupes before calling IGDB and unrecognized
    mode never calls it, so this bounds a looping client without blocking a
    genuine library backfill. The limiter needs the *request* parameter.
    """
    if body.igdb_id is not None:
        # ── igdb_id mode ────────────────────────────────────────────────────
        # 1. Dedupe BEFORE any IGDB call
        existing = (
            await db.execute(
                select(Game).where(Game.external_api_id == str(body.igdb_id))
            )
        ).scalar_one_or_none()
        if existing is not None:
            response.status_code = 200
            return _game_response(existing, None)

        # 2. Fetch from IGDB
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

        # 3. Insert enriched game row
        game = Game(
            primary_name=canonical_name,
            external_api_id=str(body.igdb_id),
            cover_image_url=meta.cover_url,
            cover_source=CoverSource.EXTERNAL,
            enrichment_status=EnrichmentStatus.ENRICHED,
            genres=meta.genres,
            themes=meta.themes,
            developers=meta.developers,
            publishers=meta.publishers,
            first_release_date=meta.first_release_date,
        )
        db.add(game)
        await db.flush()

        # 4. Optional query alias — skipped for the shared demo account, which
        # must never claim an unclaimed process name in the global alias table.
        if body.query and body.query.strip() and user.discord_id != DEMO_DISCORD_ID:
            await _add_alias_if_absent(db, game.id, body.query)

    else:
        # ── unrecognized mode ────────────────────────────────────────────────
        # 1. Insert NEEDS_REVIEW stub
        game = Game(
            primary_name=body.name,  # type: ignore[arg-type]
            enrichment_status=EnrichmentStatus.NEEDS_REVIEW,
        )
        db.add(game)
        await db.flush()

    await db.commit()
    return _game_response(game, None)


async def _add_alias_if_absent(db: AsyncSession, game_id: int, value: str) -> None:
    """Insert a GameAlias only if no row with that discord_process_name exists."""
    await add_alias(db, game_id, value)


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

# Score floors. The strict path earns the low floor from its prefilter: every
# token matched, so a weak score is a genuine-but-loose hit. The any-token
# fallback has no such guarantee, so it needs a floor above its noise band.
#
# Both bands measured against the live catalog for "the Division". Noise runs
# 0.30–0.60 — the top of it is alias-admitted: "Skyrim Special Edition" enters
# on its alias "The Elder Scrolls V: Skyrim - Special Edition" (a real
# word-boundary `The`) and then scores 0.6 on its *primary name*. Genuine
# rescues ("hades zzzznomatch" → Hades, "the divison" → The Division) run
# 0.75–0.95. 0.7 sits between the bands.
#
# Headroom above 0.7 is only 0.05, and it is a hard ceiling: _confidence caps
# any digitless query against a numbered title at _NUMBER_MISMATCH_CAP (0.75),
# so every numbered-sequel typo rescue scores exactly 0.75, however good the
# match. Raising this floor to 0.8 silently drops that whole class — see
# test_fallback_floor_stays_below_the_sequel_cap.
_SUGGEST_FLOOR = 0.3
_SUGGEST_FALLBACK_FLOOR = 0.7

# POSIX ERE metacharacters. Escaped rather than using re.escape(), whose
# output targets Python's engine — Postgres reads these patterns, and user
# input reaches the operator directly.
_ERE_METACHARS = r".^$*+?()[]{}|\\"


def _word_prefix_pattern(token: str) -> str:
    r"""Build a Postgres regex matching *token* at the start of a word.

    ``\m`` anchors to a word boundary, so `the` matches "The Division" and
    "Theme Park" but not "Wu*the*ring Waves". Prefix rather than whole-word
    semantics: the last token of a typeahead query is usually half-typed.
    """
    escaped = "".join("\\" + ch if ch in _ERE_METACHARS else ch for ch in token)
    return r"\m" + escaped


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

    Scope: the global catalog minus unclaimed unresolved rows. The catalog is
    writable by any authenticated user, so an aliasless NEEDS_REVIEW row is a
    free-text stub nobody has bound — offensive or gibberish input from one
    user would otherwise surface in everyone's typeahead. Such a row is hidden
    unless the caller has claimed it. Everything else, PENDING included, stays
    globally visible.

    Precision comes from the prefilter, not the score. Every token must match
    at a word boundary in primary_name or one of the aliases; survivors are
    then scored with _confidence() (max over primary_name + all aliases),
    dropped below the floor for the pass that found them, sorted descending,
    paginated.

    _confidence() is the enrichment scorer, tuned for `witcher3.exe` →
    canonical title — it strips whitespace and leans on partial ratios, so it
    scores short typeahead queries generously against unrelated titles
    ("the division" vs "Wuthering Waves" = 0.48). No floor separates that from
    real hits, which is why the prefilter has to carry the precision.
    """
    q = q.strip()
    if not q:
        raise HTTPException(status_code=422, detail="q must not be blank")

    tokens = q.split()

    # One condition per token: matches primary_name or any alias of the game.
    token_conditions = []
    for token in tokens:
        pattern = _word_prefix_pattern(token)
        token_conditions.append(
            or_(
                Game.primary_name.op("~*")(pattern),
                GameAlias.discord_process_name.op("~*")(pattern),
            )
        )

    # Visibility: hide unresolved rows nobody has claimed. Only NEEDS_REVIEW is
    # ever hidden — hiding PENDING too would split libraries, since a bot-created
    # row is PENDING until the enrichment worker runs and another user, unable to
    # see it, would create a duplicate the bot never writes to.
    #
    # A separate aliased(GameAlias) is required: _id_query already outerjoins
    # GameAlias for token matching, and a bare exists() would correlate against
    # that join instead of opening a fresh subquery.
    _claim_alias = aliased(GameAlias)
    visible_game = or_(
        Game.enrichment_status != EnrichmentStatus.NEEDS_REVIEW,
        # An alias means a bot bound a real process to this row.
        exists().where(_claim_alias.game_id == Game.id),
        # deleted_at.is_(None) rather than visible_session() on purpose: flicker
        # sessions still count as a claim. The question here is "is this game
        # mine", not "does it count toward stats" — do not harmonize this with
        # resolve_game.
        exists().where(
            and_(
                GameSession.game_id == Game.id,
                GameSession.user_id == user.discord_id,
                GameSession.deleted_at.is_(None),
            )
        ),
        # Any preference row counts, is_ignored=True included: ignoring is a
        # library-display choice, and suggest exists to attach a session.
        exists().where(
            and_(
                UserGamePreference.game_id == Game.id,
                UserGamePreference.user_id == user.discord_id,
            )
        ),
    )

    def _id_query(predicate):
        # visible_game is and_ed in here so both the strict and the fallback
        # pass get it, and `total` reflects the filtered set.
        return (
            select(Game.id)
            .outerjoin(GameAlias, GameAlias.game_id == Game.id)
            .where(and_(predicate, visible_game))
            .distinct()
        )

    # Step 1: require every token to match somewhere on the game. A single
    # weak token must not be enough to admit one.
    matched_ids = (
        await db.execute(_id_query(and_(*token_conditions)))
    ).scalars().all()

    # A typo in any one token would otherwise zero out the whole result set,
    # so fall back to any-token once — recall only when strict found nothing.
    floor = _SUGGEST_FLOOR
    if not matched_ids and len(token_conditions) > 1:
        matched_ids = (
            await db.execute(_id_query(or_(*token_conditions)))
        ).scalars().all()
        floor = _SUGGEST_FALLBACK_FLOOR

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
        if score >= floor:
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
# POST /match
# ---------------------------------------------------------------------------

@router.post("/match", response_model=list[IGDBCandidateOut])
@limiter.limit("20/hour")
async def match_game(
    request: Request,
    body: GameMatchRequest,
    user: User = Depends(get_current_user),
):
    """
    Search IGDB synchronously and return ranked candidates for *body.query*.

    Intended for the wizard's "search online" step when library suggest is empty.
    No DB write — callers receive a pick-list and submit the chosen IGDB id to
    POST /sessions or another endpoint.

    Every call spends one request from the IGDB budget, which Twitch throttles
    per Client ID (4 req/s) — shared by every user *and* the Celery enrichment
    worker. Hence the 20/hour per-credential cap: generous for honest wizard
    use, and it stops one looping client from starving the rest.
    The limiter requires the *request* parameter.

    Errors:
      503 — IGDB rate-limited or auth expired (_RateLimited)
      502 — any other IGDB failure
      429 — caller exceeded 20/hour
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
# GET /{game_id}
# ---------------------------------------------------------------------------

@router.get("/{game_id}", response_model=GameResponse)
async def get_game(
    game_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Single game by id, same shape as GET /games list items.

    Access is session-derived: 404 unless the caller has at least one visible
    session for the game (also covers a non-existent id). Deliberately does NOT
    apply is_ignored / library-visibility filters — the caller navigated here by
    id, so an ignored or NEEDS_REVIEW game still resolves (mirrors /{id}/stats).
    total_seconds/last_played match the library card (COMPLETED + ONGOING-live).
    """
    row = (
        await db.execute(
            select(Game, _playtime_expr(), _last_played_expr())
            .join(GameSession, GameSession.game_id == Game.id)
            .where(
                GameSession.user_id == user.discord_id,
                GameSession.game_id == game_id,
                *visible_session(),
            )
            .group_by(Game.id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Game not found")

    pref = (
        await db.execute(
            select(UserGamePreference).where(
                UserGamePreference.user_id == user.discord_id,
                UserGamePreference.game_id == game_id,
            )
        )
    ).scalar_one_or_none()

    return _game_response(
        row[0], pref, total_seconds=row.total_seconds, last_played=row.last_played
    )
