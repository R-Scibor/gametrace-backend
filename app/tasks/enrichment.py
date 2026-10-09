"""
Celery task: enrich a game record with metadata from IGDB (primary) and Steam API (fallback).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MATCHING PIPELINE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Step 1 — _sanitize(s)
  Normalises a raw game name (or Discord process name) into a
  comparable form. Applied to BOTH sides of every comparison.

  Order of operations:
    1. lowercase
    2. strip file extension          "witcher3.exe"   → "witcher3"
    3. remove [bracketed] content    "Hades [GOTY]"   → "Hades"
    4. remove (parenthesised) content "Game (2023)"   → "Game"
       ⚠ entire parenthesised block is dropped, including its
         content — "Dark Souls (Remastered)" loses "Remastered".
         Confidence still passes via WRatio partial matching.
    5. & → "and"
    6. structural separators (: - _) → space
    7. strip remaining non-alphanumeric chars (apostrophes, accents…)
    8. drop a bare "demo" / "playtest" token when another alphabetic word
       of 4+ letters remains. "Keep It Up! Demo" → "keep it up".
       "Democracy 4" is unchanged. "The Demo" and "Demo 2" keep the
       qualifier: the leftover would be the search query "the" or "2".
       A same-name IGDB tie (three "Keep It Up!" rows) still goes to
       NEEDS_REVIEW. See docs/game-matching.md.
    9. map standalone roman numeral tokens i–xv to arabic digits
       "Diablo IV" → "diablo 4",  "Final Fantasy XV" → "final fantasy 15"
       ⚠ standalone "i" and "v" are caught by this map — game titles
         containing these as words (e.g. "I Am Alive") get digits injected.
         Cross-game comparisons involving such titles may produce unexpected
         number sets; same-game comparisons are unaffected (both sides transform
         identically).
   10. collapse whitespace, strip → words remain space-separated

  ⚠ _sanitize keeps word boundaries. Earlier versions glued tokens into a
    single string ("the witcher 3 wild hunt" → "thewitcher3wildhunt"); that
    helped _confidence's substring trick but killed recall when the same
    output was used as the IGDB / Steam search term. IGDB and Steam run
    word-tokenized full-text search and return zero hits for glued blobs
    (e.g. "thefarmerwasreplaced", "europauniversalis5"). The space-collapse
    now lives inside _confidence (Step 2) where it's actually needed.
    See docs/game-matching.md "Search-query vs scoring" gotcha.

Step 2 — _confidence(a, b) → float [0.0, 1.0]
  a.  Sanitize both sides, then strip remaining whitespace before scoring.
      The whitespace strip is local to _confidence — it lets WRatio's
      partial_ratio find "witcher3" as a substring of "thewitcher3wildhunt"
      (~0.90); without it the same pair reaches only ~0.80 because the space
      between "witcher" and "3" breaks substring alignment. Applied to both
      sides — comparison stays symmetric.

  b.  Compute fuzz.WRatio on the collapsed forms.
      WRatio picks the best of ratio / partial_ratio /
      token_sort_ratio / token_set_ratio, so subtitles, word-
      order differences, and partial containment are all handled.

  c.  Number guard (NUMBER_MISMATCH_CAP = 0.75):
      Extract all digit sequences from each sanitized string.
      If the two sets differ AND at least one string contains digits,
      cap the score at 0.75 (below the 0.85 CONFIDENCE_THRESHOLD).

      Rationale: WRatio's token_set_ratio sees "hades" as fully
      contained in "hades 2" and returns ~0.95 — indistinguishable
      from the same game. A number mismatch means a different series
      entry: Hades vs Hades II, Diablo 3 vs Diablo 4, FIFA 23 vs FIFA 24.

      Same number → no penalty:
        "The Witcher 3" vs "The Witcher 3: Wild Hunt"  → {3} == {3}  ✓
        "Cyberpunk 2077" vs "Cyberpunk 2077: Phantom Liberty" → {2077} == {2077}  ✓

      ⚠ Known limitation: architecture/API-version numbers embedded in
        process names (Win64, dx11, x64, game64) contain digits that
        may mismatch a canonical game name with no number, triggering a
        false cap. Platform token stripping is not implemented — if these
        patterns appear in Discord activity names, the enrichment falls
        through to Steam or NEEDS_REVIEW.

Step 3 — _igdb_search(name) → IGDBResult(cover_url, confidence, genres, themes,
  developers, publishers, first_release_date, name, igdb_id)
  - Sends _sanitize(name) as the IGDB search query to strip process-name
    noise before the API call.
  - Requests alternative_names.name alongside the primary name field.
  - Scores every candidate (primary + all alternative names) with
    _confidence(original_name, candidate); takes the maximum.
  - Stores the chosen hit's name and numeric id on the result. A miss, or a
    hit whose id is missing or < 1, leaves name/igdb_id as None.
  - Normalises returned cover URLs:
      protocol-relative "//…" → "https://…"
      /t_thumb/ → /t_cover_big/  (vertical box art, ~264×352 px)
  - Two or more distinct ids (int >= 1) that share the top score, when that
    score is >= 0.85, return ambiguous=True and no id, name, cover, or
    metadata. Confidence stays the shared score. One row counts once: the
    score is the max of its primary name and its alternative names. A tie
    below 0.85 stays an ordinary miss.

Step 4 — _steam_search(name) → (app_id | None, cover_url | None, name | None)
  Fuzzy match against Steam Store search results using the same _confidence()
  pipeline (sanitize both sides, WRatio, number guard) and CONFIDENCE_THRESHOLD.
  The module calls a score >= 0.85 a Steam exact match. It is not string equality.
  An item with a missing id or an id < 1 is skipped.
  Takes the highest-scoring candidate; returns (None, None, None) if none reach 0.85.
  Cover: library_600x900.jpg (vertical portrait, same aspect ratio).

Step 5 — Pipeline decision
  The name is loaded and that session is closed before either HTTP call.
  The write session then loads the row with SELECT … FOR UPDATE. That load
  is the empty-id check. A bare legacy external_api_id counts as set.

  IGDB confidence >= 0.85, with an id >= 1 and a name:
    empty id → ENRICHED, external_api_id = igdb:{id}, primary_name = IGDB name
    id already set → refresh genres, themes, developers, publishers, and
    first_release_date. Do not change the id or the title.
  IGDB ambiguous (two ids share a top score >= 0.85):
    Do not call Steam. Do not apply either candidate.
    The locked row is ENRICHED → leave status, id, title, cover, and
    metadata unchanged.
    Otherwise → NEEDS_REVIEW.
  Otherwise a Steam score >= 0.85:
    empty id → ENRICHED, external_api_id = steam:{app_id}, primary_name = Steam name
    id already set → leave the id, the title, and the metadata columns alone
  Otherwise, when both lookups returned a parsed non-hit and the locked
  row is not ENRICHED, NEEDS_REVIEW. A high score with no usable id is a
  non-hit. A failure leaves the status unchanged, except a Steam hit,
  including the hit on an exhausted IGDB outage.

  A unique violation on uq_games_external_api_id rolls that write back, logs
  enrich_game.external_id_taken, and returns. The row is not demoted.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OPERATIONAL NOTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Exponential backoff on HTTP 429: 2^retry * 60s countdown (max 5 retries).
task_id="enrich_game_{game_id}" is the Celery result key. It is not a lock.
A retryable IGDB failure waits 2^retry * 60s, up to 5 retries. The attempt
whose cap is already spent calls Steam once and does not schedule another wait.
Custom covers: cover_image_url and cover_source stay when cover_source=CUSTOM.
An IGDB hit still writes genres, themes, developers, publishers, and
first_release_date on a CUSTOM row. A null IGDB cover does not clear a cover
the worker already stored.

Event loop note: asyncpg connections are bound to the loop they were created on.
Reusing the global AsyncSessionLocal across multiple asyncio.run() calls causes
"Future attached to a different loop" errors. Fix: one asyncio.run() per task,
fresh engine created inside it, sync HTTP calls via asyncio.to_thread().
"""
import asyncio
import json
import logging

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.game import CoverSource, EnrichmentStatus, Game
from app.services.enrichment_dispatch import queue_enrichment
from app.services.external_ids import is_external_id_conflict, steam_external_id
from app.services.game_matching import (
    CONFIDENCE_THRESHOLD,
    IGDBResult,
    _confidence,
    _empty_igdb_result,
    _igdb_search,
    _RateLimited,
    _sanitize,
    apply_igdb_metadata,
)
from app.services.game_review import sync_review_preferences

logger = logging.getLogger(__name__)


class LookupRetryable(Exception):
    """A lookup failed in a way the next attempt might survive."""

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__(source)


def _lookup_kind(exc: BaseException) -> str:
    """Return ``retry``, ``unanswered``, or ``unexpected``.

    ``json.JSONDecodeError`` is a ``ValueError``. It must be matched before
    any broader ``ValueError`` branch. This function has no ``ValueError``
    branch: a parse bug falls through to ``unexpected``.
    """
    if isinstance(exc, _RateLimited):
        return "retry"
    if isinstance(exc, httpx.TransportError):
        return "retry"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 429) or code >= 500:
            return "retry"
        return "unanswered"
    if isinstance(exc, json.JSONDecodeError):
        return "unanswered"
    return "unexpected"


def _steam_search(name: str) -> tuple[int | None, str | None, str | None]:
    """Return (app_id, cover_url, name) when the best score is >= 0.85.

    The threshold is the same fuzzy _confidence score as IGDB. It is not
    string equality. A missing id or an id < 1 is not a candidate.
    """
    with httpx.Client(timeout=10) as client:
        resp = client.get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": _sanitize(name), "l": "english", "cc": "US"},
        )

    if resp.status_code == 429:
        raise _RateLimited("Steam")
    resp.raise_for_status()

    best_score = 0.0
    best_app_id: int | None = None
    best_cover: str | None = None
    best_name: str | None = None

    for item in resp.json().get("items", []):
        item_name = item.get("name", "")
        if not item_name:
            continue
        raw_id = item.get("id")
        try:
            app_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if isinstance(raw_id, bool) or app_id < 1:
            continue
        score = _confidence(name, item_name)
        if score > best_score:
            best_score = score
            best_app_id = app_id
            best_name = item_name
            best_cover = (
                f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/library_600x900.jpg"
            )

    if best_score >= CONFIDENCE_THRESHOLD and best_app_id is not None and best_name:
        return best_app_id, best_cover, best_name
    return None, None, None


def _igdb_hit(result: IGDBResult) -> bool:
    return (
        result.confidence >= CONFIDENCE_THRESHOLD
        and isinstance(result.igdb_id, int)
        and not isinstance(result.igdb_id, bool)
        and result.igdb_id >= 1
        and bool(result.name)
    )


def _apply_steam(
    game: Game,
    *,
    app_id: int,
    cover_url: str | None,
    name: str,
    replace_identity: bool,
) -> None:
    """Steam has no genres, companies, or release date. Never clear those columns."""
    if replace_identity:
        game.external_api_id = steam_external_id(app_id)
        game.primary_name = name
    if game.cover_source != CoverSource.CUSTOM and cover_url is not None:
        game.cover_image_url = cover_url


async def _write_enrichment(
    db: AsyncSession,
    game_id: int,
    *,
    igdb_result: IGDBResult,
    steam: tuple[int | None, str | None, str | None],
) -> tuple[EnrichmentStatus, str | None, str | None]:
    """Lock the row and write one enrich attempt. The caller commits nothing else.

    Returns (status, cover_url, external_api_id). On a unique violation the
    returned triple is the row from before this attempt, and the savepoint
    is already rolled back.
    """
    result = await db.execute(
        select(Game).where(Game.id == game_id).with_for_update()
    )
    game = result.scalar_one_or_none()
    if game is None:
        raise LookupError(game_id)

    prior_status = game.enrichment_status
    prior_cover = game.cover_image_url
    prior_external_id = game.external_api_id
    identity_empty = prior_external_id is None

    if _igdb_hit(igdb_result):
        assert igdb_result.igdb_id is not None
        assert igdb_result.name is not None
        try:
            async with db.begin_nested():
                await apply_igdb_metadata(
                    db,
                    game,
                    igdb_result.name,
                    igdb_result,
                    igdb_id=igdb_result.igdb_id,
                    replace_identity=identity_empty,
                    clear_cover_on_null=False,
                )
                await db.flush()
        except IntegrityError as exc:
            if not is_external_id_conflict(exc):
                raise
            logger.info(
                "enrich_game.external_id_taken",
                extra={"game_id": game_id},
            )
            return prior_status, prior_cover, prior_external_id
        await db.commit()
        return game.enrichment_status, igdb_result.cover_url, game.external_api_id

    app_id, steam_cover, steam_name = steam
    if app_id is not None and steam_name:
        try:
            async with db.begin_nested():
                _apply_steam(
                    game,
                    app_id=app_id,
                    cover_url=steam_cover,
                    name=steam_name,
                    replace_identity=identity_empty,
                )
                game.enrichment_status = EnrichmentStatus.ENRICHED
                await sync_review_preferences(
                    db,
                    game_id,
                    previous_status=prior_status,
                    new_status=EnrichmentStatus.ENRICHED,
                )
                await db.flush()
        except IntegrityError as exc:
            if not is_external_id_conflict(exc):
                raise
            logger.info(
                "enrich_game.external_id_taken",
                extra={"game_id": game_id},
            )
            return prior_status, prior_cover, prior_external_id
        await db.commit()
        return EnrichmentStatus.ENRICHED, steam_cover, game.external_api_id

    if game.enrichment_status == EnrichmentStatus.ENRICHED:
        logger.info(
            "enrich_game.miss_kept_enriched",
            extra={"game_id": game_id},
        )
        return prior_status, prior_cover, prior_external_id

    game.enrichment_status = EnrichmentStatus.NEEDS_REVIEW
    await sync_review_preferences(
        db,
        game_id,
        previous_status=prior_status,
        new_status=EnrichmentStatus.NEEDS_REVIEW,
    )
    await db.commit()
    return EnrichmentStatus.NEEDS_REVIEW, None, game.external_api_id


# ---------------------------------------------------------------------------
# Single async function — owns its own engine for this event loop
# ---------------------------------------------------------------------------

async def _run_enrichment(
    game_id: int,
    *,
    retries: int = 0,
    max_retries: int = 5,
) -> tuple[EnrichmentStatus, str | None, str | None]:
    """Return ``(status, cover_url, external_api_id)``.

    Raises ``LookupRetryable`` when a lookup should be tried again and
    ``retries`` is still below ``max_retries``. Raises ``LookupError`` when
    the row is gone. Any other exception from a lookup propagates.

    The name is read and the session closed before HTTP. The empty-id
    decision and the ``ENRICHED`` guard are the locked load inside
    ``_write_enrichment``, after HTTP.
    """
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with SessionLocal() as db:
            game = await db.get(Game, game_id)
            if game is None:
                raise LookupError(game_id)
            name: str = game.primary_name
            prior_status = game.enrichment_status
            prior_cover = game.cover_image_url
            prior_external_id = game.external_api_id

        igdb_result: IGDBResult = _empty_igdb_result()
        igdb_state = "unanswered"
        try:
            igdb_result = await asyncio.to_thread(_igdb_search, name)
            igdb_state = "answered"
        except Exception as exc:
            kind = _lookup_kind(exc)
            if kind == "unexpected":
                raise
            if kind == "retry" and retries < max_retries:
                raise LookupRetryable("igdb") from exc
            if kind == "retry":
                logger.error(
                    "enrich_game.retries_exhausted",
                    extra={"game_id": game_id, "source": "igdb"},
                )
                igdb_state = "exhausted"
            else:
                logger.warning(
                    "enrich_game.lookup_unanswered",
                    extra={"game_id": game_id, "source": "igdb"},
                )
                igdb_state = "unanswered"

        steam: tuple[int | None, str | None, str | None] = (None, None, None)
        call_steam = (
            (igdb_state != "answered" or not _igdb_hit(igdb_result))
            and not igdb_result.ambiguous
        )
        if call_steam:
            try:
                steam = await asyncio.to_thread(_steam_search, name)
            except Exception as exc:
                kind = _lookup_kind(exc)
                if kind == "unexpected":
                    raise
                if kind == "retry" and retries < max_retries:
                    raise LookupRetryable("steam") from exc
                if kind == "retry":
                    logger.error(
                        "enrich_game.retries_exhausted",
                        extra={"game_id": game_id, "source": "steam"},
                    )
                else:
                    logger.warning(
                        "enrich_game.lookup_unanswered",
                        extra={"game_id": game_id, "source": "steam"},
                    )
                return prior_status, prior_cover, prior_external_id

        igdb_hit = igdb_state == "answered" and _igdb_hit(igdb_result)
        steam_hit = steam[0] is not None and bool(steam[2])
        both_non_hits = igdb_state == "answered" and not igdb_hit
        if not (igdb_hit or steam_hit or both_non_hits):
            return prior_status, prior_cover, prior_external_id

        async with SessionLocal() as db:
            return await _write_enrichment(
                db, game_id, igdb_result=igdb_result, steam=steam,
            )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

def _retry_source(exc: BaseException) -> str:
    source = getattr(exc, "source", None)
    if isinstance(source, str) and source in ("igdb", "steam"):
        return source
    if "steam" in str(exc).lower():
        return "steam"
    return "igdb"


# rate_limit is per-worker process — fine for a single container, but must be
# revisited (e.g. Redis-based token bucket) if multiple worker instances are added.
@celery_app.task(name="tasks.enrich_game", bind=True, max_retries=5, rate_limit="1/s")
def enrich_game(self, game_id: int) -> None:
    try:
        status, cover_url, ext_id = asyncio.run(
            _run_enrichment(
                game_id,
                retries=self.request.retries,
                max_retries=self.max_retries,
            )
        )
        logger.info(
            "enrich_game: game_id=%d → %s (cover=%s, ext_id=%s)",
            game_id, status, cover_url, ext_id,
        )

    except LookupError:
        logger.warning("enrich_game.not_found", extra={"game_id": game_id})

    except (_RateLimited, LookupRetryable) as exc:
        if self.request.retries >= self.max_retries:
            logger.error(
                "enrich_game.retries_exhausted",
                extra={"game_id": game_id, "source": _retry_source(exc)},
            )
            return
        countdown = (2 ** self.request.retries) * 60
        logger.warning(
            "enrich_game.lookup_retry",
            extra={
                "game_id": game_id,
                "source": _retry_source(exc),
                "countdown": countdown,
            },
        )
        raise self.retry(exc=exc, countdown=countdown) from exc

    except Exception:
        logger.exception("enrich_game.unexpected_error", extra={"game_id": game_id})
        raise


# ---------------------------------------------------------------------------
# One-shot backfill task — re-queues legacy ENRICHED games missing metadata
# ---------------------------------------------------------------------------

async def _run_backfill(batch_size: int, full: bool = False) -> int:
    """Iterate ENRICHED games in chunks; dispatch enrich_game per row.

    When *full* is False (default) only games with an empty genres array are
    queued.  When *full* is True every ENRICHED game is re-queued regardless
    of genre data.

    Returns the total number of games queued.
    """
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    queued = 0
    last_id = 0
    try:
        while True:
            async with SessionLocal() as db:
                conditions = [
                    Game.enrichment_status == EnrichmentStatus.ENRICHED,
                    Game.id > last_id,
                ]
                if not full:
                    conditions.append(func.jsonb_array_length(Game.genres) == 0)
                stmt = (
                    select(Game.id)
                    .where(*conditions)
                    .order_by(Game.id)
                    .limit(batch_size)
                )
                result = await db.execute(stmt)
                rows = list(result.scalars().all())

            if not rows:
                break

            for game_id in rows:
                queue_enrichment(game_id)
                queued += 1

            last_id = rows[-1]
            if len(rows) < batch_size:
                break

        return queued
    finally:
        await engine.dispose()


@celery_app.task(name="tasks.backfill_metadata")
def backfill_metadata(batch_size: int = 500, full: bool = False) -> int:
    """Re-queue ENRICHED games for re-enrichment.

    When *full* is False (default) only games with an empty genres array are
    queued.  Pass ``full=True`` to re-fetch every ENRICHED game regardless of
    genre data.

    Returns the number of games queued.
    """
    queued = asyncio.run(_run_backfill(batch_size, full=full))
    logger.info("backfill_metadata: queued %d games for re-enrichment", queued)
    return queued
