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
    8. map standalone roman numeral tokens i–xv to arabic digits
       "Diablo IV" → "diablo 4",  "Final Fantasy XV" → "final fantasy 15"
       ⚠ standalone "i" and "v" are caught by this map — game titles
         containing these as words (e.g. "I Am Alive") get digits injected.
         Cross-game comparisons involving such titles may produce unexpected
         number sets; same-game comparisons are unaffected (both sides transform
         identically).
    9. collapse whitespace, strip → words remain space-separated

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

Step 3 — _igdb_search(name) → IGDBResult(cover_url, confidence, genres, themes, developers, publishers, first_release_date)
  - Sends _sanitize(name) as the IGDB search query to strip process-name
    noise before the API call.
  - Requests alternative_names.name alongside the primary name field.
  - Scores every candidate (primary + all alternative names) with
    _confidence(original_name, candidate); takes the maximum.
  - Normalises returned cover URLs:
      protocol-relative "//…" → "https://…"
      /t_thumb/ → /t_cover_big/  (vertical box art, ~264×352 px)

Step 4 — _steam_search(name) → (app_id | None, cover_url | None)
  Fuzzy match against Steam Store search results using the same _confidence()
  pipeline (sanitize both sides, WRatio, number guard) and CONFIDENCE_THRESHOLD.
  Takes the highest-scoring candidate; returns (None, None) if none reach 0.85.
  Cover: library_600x900.jpg (vertical portrait, same aspect ratio).

Step 5 — Pipeline decision
  IGDB confidence >= 0.85  →  ENRICHED  (IGDB cover)
  IGDB confidence <  0.85, Steam exact match found  →  ENRICHED  (Steam cover)
  Neither  →  NEEDS_REVIEW  (human review required in admin UI)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OPERATIONAL NOTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Exponential backoff on HTTP 429: 2^retry * 60s countdown (max 5 retries).
Redis deduplication: task_id="enrich_game_{game_id}" — one task per game queued at a time.
Custom covers: cover_image_url is NOT updated when cover_source=CUSTOM.

Event loop note: asyncpg connections are bound to the loop they were created on.
Reusing the global AsyncSessionLocal across multiple asyncio.run() calls causes
"Future attached to a different loop" errors. Fix: one asyncio.run() per task,
fresh engine created inside it, sync HTTP calls via asyncio.to_thread().
"""
import asyncio
import logging
import re
from datetime import date
from typing import NamedTuple

import httpx
from rapidfuzz import fuzz
from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.game import CoverSource, EnrichmentStatus, Game
from app.tasks.igdb_auth import get_igdb_token, invalidate_igdb_token

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.85
# Score ceiling applied when sanitized digit sets differ (sequel guard).
# Must stay below CONFIDENCE_THRESHOLD so mismatched-number pairs never enrich.
_NUMBER_MISMATCH_CAP = 0.75

_ROMAN_MAP = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
    "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15",
}


# ---------------------------------------------------------------------------
# Custom exception to signal 429 back to the sync Celery task for retry
# ---------------------------------------------------------------------------

class _RateLimited(Exception):
    pass


# ---------------------------------------------------------------------------
# Sync HTTP helpers — called via asyncio.to_thread()
# ---------------------------------------------------------------------------

def _sanitize(s: str) -> str:
    s = s.lower()
    s = re.sub(r'\.\w{2,5}$', '', s)              # strip file extension (.exe, .app)
    s = re.sub(r'[\[\(][^\]\)]*[\]\)]', '', s)    # remove [tags] and (tags)
    s = s.replace('&', 'and')                      # & → and
    s = re.sub(r'[:\-_]', ' ', s)                 # structural separators → space
    s = re.sub(r'[^a-z0-9\s]', '', s)             # strip remaining non-alphanumeric
    tokens = [_ROMAN_MAP.get(t, t) for t in s.split()]
    # Words stay space-separated. The space-collapse trick (for substring
    # alignment of exe-style names) lives inside _confidence — gluing here
    # would break IGDB / Steam search recall on multi-word titles.
    return ' '.join(tokens)


def _confidence(a: str, b: str) -> float:
    # Strip whitespace from sanitized forms so partial_ratio finds exe-style
    # names (e.g. "witcher3") as substrings of canonical titles
    # ("thewitcher3wildhunt"). Applied symmetrically; scoring-only.
    sa = _sanitize(a).replace(' ', '')
    sb = _sanitize(b).replace(' ', '')
    score = fuzz.WRatio(sa, sb) / 100.0

    nums_a = set(re.findall(r'\d+', sa))
    nums_b = set(re.findall(r'\d+', sb))
    if (nums_a or nums_b) and nums_a != nums_b:
        score = min(score, _NUMBER_MISMATCH_CAP)

    return score


class IGDBResult(NamedTuple):
    cover_url: str | None
    confidence: float
    genres: list[str]
    themes: list[str]
    developers: list[str]
    publishers: list[str]
    first_release_date: date | None


def _empty_igdb_result() -> IGDBResult:
    return IGDBResult(
        cover_url=None,
        confidence=0.0,
        genres=[],
        themes=[],
        developers=[],
        publishers=[],
        first_release_date=None,
    )


def _igdb_search(name: str) -> IGDBResult:
    """Returns IGDBResult with cover, confidence, and metadata for the best candidate.

    Raises _RateLimited on HTTP 429 or 401.
    """
    if not settings.igdb_client_id or not settings.igdb_client_secret:
        logger.warning("IGDB credentials not set — skipping IGDB search")
        return _empty_igdb_result()

    token = get_igdb_token()
    clean_name = _sanitize(name)
    safe_name = clean_name.replace('"', '\\"')

    with httpx.Client(timeout=10) as client:
        resp = client.post(
            "https://api.igdb.com/v4/games",
            headers={
                "Client-ID": settings.igdb_client_id,
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/plain",
            },
            content=(
                f'search "{safe_name}"; '
                'fields name,cover.url,cover.image_id,alternative_names.name,'
                'genres.name,themes.name,'
                'involved_companies.company.name,involved_companies.developer,'
                'involved_companies.publisher,first_release_date; '
                'limit 5;'
            ),
        )

    if resp.status_code == 401:
        invalidate_igdb_token()
        raise _RateLimited("IGDB-auth")  # triggers Celery backoff retry

    if resp.status_code == 429:
        raise _RateLimited("IGDB")

    resp.raise_for_status()

    best_score = 0.0
    best_cover: str | None = None
    best_genres: list[str] = []
    best_themes: list[str] = []
    best_developers: list[str] = []
    best_publishers: list[str] = []
    best_release: date | None = None

    for game in resp.json():
        candidate_names = [game.get("name", "")]
        for alt in game.get("alternative_names", []):
            if alt.get("name"):
                candidate_names.append(alt["name"])
        score = max(_confidence(name, n) for n in candidate_names if n)

        if score > best_score:
            best_score = score

            cover = game.get("cover")
            if cover and cover.get("url"):
                url = cover["url"]
                if url.startswith("//"):
                    url = "https:" + url
                url = url.replace("/t_thumb/", "/t_cover_big/")
                best_cover = url
            else:
                best_cover = None

            best_genres = [g["name"] for g in game.get("genres", []) if g.get("name")]
            best_themes = [t["name"] for t in game.get("themes", []) if t.get("name")]
            best_developers = [
                ic["company"]["name"]
                for ic in game.get("involved_companies", [])
                if ic.get("developer") and ic.get("company", {}).get("name")
            ]
            best_publishers = [
                ic["company"]["name"]
                for ic in game.get("involved_companies", [])
                if ic.get("publisher") and ic.get("company", {}).get("name")
            ]
            ts = game.get("first_release_date")
            best_release = date.fromtimestamp(ts) if ts else None

    return IGDBResult(
        cover_url=best_cover,
        confidence=best_score,
        genres=best_genres,
        themes=best_themes,
        developers=best_developers,
        publishers=best_publishers,
        first_release_date=best_release,
    )


def _steam_search(name: str) -> tuple[str | None, str | None]:
    """Returns (app_id, cover_url) on confident match, else (None, None). Raises _RateLimited on 429."""
    with httpx.Client(timeout=10) as client:
        resp = client.get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": _sanitize(name), "l": "english", "cc": "US"},
        )

    if resp.status_code == 429:
        raise _RateLimited("Steam")
    resp.raise_for_status()

    best_score = 0.0
    best_app_id: str | None = None
    best_cover: str | None = None

    for item in resp.json().get("items", []):
        item_name = item.get("name", "")
        if not item_name:
            continue
        score = _confidence(name, item_name)
        if score > best_score:
            best_score = score
            best_app_id = str(item["id"])
            best_cover = f"https://cdn.akamai.steamstatic.com/steam/apps/{best_app_id}/library_600x900.jpg"

    if best_score >= CONFIDENCE_THRESHOLD:
        return best_app_id, best_cover

    return None, None


# ---------------------------------------------------------------------------
# Single async function — owns its own engine for this event loop
# ---------------------------------------------------------------------------

async def _run_enrichment(game_id: int) -> tuple[EnrichmentStatus, str | None, str | None]:
    """
    Returns (status, cover_url, external_api_id).
    Raises _RateLimited if an API returns HTTP 429.
    Raises LookupError if the game row is not found.
    """
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    try:
        # ── Read game name ───────────────────────────────────────────────────
        async with SessionLocal() as db:
            game = await db.get(Game, game_id)
            if game is None:
                raise LookupError(game_id)
            name: str = game.primary_name

        # ── IGDB (sync HTTP in thread pool) ──────────────────────────────────
        igdb_result: IGDBResult = _empty_igdb_result()
        try:
            igdb_result = await asyncio.to_thread(_igdb_search, name)
        except _RateLimited:
            raise
        except Exception:
            logger.exception("enrich_game: IGDB lookup failed for game_id=%d", game_id)

        if igdb_result.confidence >= CONFIDENCE_THRESHOLD:
            async with SessionLocal() as db:
                await _apply(
                    db,
                    game_id,
                    EnrichmentStatus.ENRICHED,
                    igdb_result.cover_url,
                    None,
                    metadata=igdb_result,
                )
            return EnrichmentStatus.ENRICHED, igdb_result.cover_url, None

        # ── Steam fallback ───────────────────────────────────────────────────
        steam_id: str | None = None
        steam_cover: str | None = None
        try:
            steam_id, steam_cover = await asyncio.to_thread(_steam_search, name)
        except _RateLimited:
            raise
        except Exception:
            logger.exception("enrich_game: Steam lookup failed for game_id=%d", game_id)

        if steam_id is not None:
            async with SessionLocal() as db:
                await _apply(db, game_id, EnrichmentStatus.ENRICHED, steam_cover, steam_id)
            return EnrichmentStatus.ENRICHED, steam_cover, steam_id

        # ── No match ─────────────────────────────────────────────────────────
        async with SessionLocal() as db:
            await _apply(db, game_id, EnrichmentStatus.NEEDS_REVIEW, None, None)
        return EnrichmentStatus.NEEDS_REVIEW, None, None

    finally:
        await engine.dispose()


async def _save_needs_review(game_id: int) -> None:
    """Fallback write used by error/retry handlers."""
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with SessionLocal() as db:
            await _apply(db, game_id, EnrichmentStatus.NEEDS_REVIEW, None, None)
    finally:
        await engine.dispose()


async def _apply(
    db: AsyncSession,
    game_id: int,
    status: EnrichmentStatus,
    cover_url: str | None,
    external_api_id: str | None,
    *,
    metadata: IGDBResult | None = None,
) -> None:
    game = await db.get(Game, game_id)
    if game is None:
        return
    game.enrichment_status = status
    if external_api_id is not None:
        game.external_api_id = external_api_id
    if game.cover_source != CoverSource.CUSTOM:
        if cover_url is not None:
            game.cover_image_url = cover_url
        if metadata is not None:
            game.genres = metadata.genres
            game.themes = metadata.themes
            game.developers = metadata.developers
            game.publishers = metadata.publishers
            game.first_release_date = metadata.first_release_date
    await db.commit()


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

# rate_limit is per-worker process — fine for a single container, but must be
# revisited (e.g. Redis-based token bucket) if multiple worker instances are added.
@celery_app.task(name="tasks.enrich_game", bind=True, max_retries=5, rate_limit="1/s")
def enrich_game(self, game_id: int) -> None:
    try:
        status, cover_url, ext_id = asyncio.run(_run_enrichment(game_id))
        logger.info(
            "enrich_game: game_id=%d → %s (cover=%s, ext_id=%s)",
            game_id, status, cover_url, ext_id,
        )

    except LookupError:
        logger.warning("enrich_game: game_id=%d not found in DB", game_id)

    except _RateLimited as exc:
        countdown = (2 ** self.request.retries) * 60
        logger.warning(
            "enrich_game: %s 429 for game_id=%d, retrying in %ds", exc, game_id, countdown,
        )
        raise self.retry(exc=exc, countdown=countdown)

    except MaxRetriesExceededError:
        logger.error("enrich_game: game_id=%d max retries exceeded → NEEDS_REVIEW", game_id)
        asyncio.run(_save_needs_review(game_id))

    except Exception:
        logger.exception("enrich_game: unexpected error for game_id=%d", game_id)
        asyncio.run(_save_needs_review(game_id))


# ---------------------------------------------------------------------------
# One-shot backfill task — re-queues legacy ENRICHED games missing metadata
# ---------------------------------------------------------------------------

async def _run_backfill(batch_size: int) -> int:
    """Iterate ENRICHED games with empty genres in chunks; dispatch enrich_game per row.

    Returns the total number of games queued.
    """
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    queued = 0
    last_id = 0
    try:
        while True:
            async with SessionLocal() as db:
                stmt = (
                    select(Game.id)
                    .where(
                        Game.enrichment_status == EnrichmentStatus.ENRICHED,
                        func.jsonb_array_length(Game.genres) == 0,
                        Game.id > last_id,
                    )
                    .order_by(Game.id)
                    .limit(batch_size)
                )
                result = await db.execute(stmt)
                rows = list(result.scalars().all())

            if not rows:
                break

            for game_id in rows:
                enrich_game.apply_async(
                    args=[game_id],
                    task_id=f"enrich_game_{game_id}",
                )
                queued += 1

            last_id = rows[-1]
            if len(rows) < batch_size:
                break

        return queued
    finally:
        await engine.dispose()


@celery_app.task(name="tasks.backfill_metadata")
def backfill_metadata(batch_size: int = 500) -> int:
    """Re-queue every ENRICHED game whose genres array is empty for re-enrichment.

    Returns the number of games queued. Idempotent (re-queueing relies on the
    enrich_game dedup key task_id=enrich_game_{game_id}).
    """
    queued = asyncio.run(_run_backfill(batch_size))
    logger.info("backfill_metadata: queued %d games for re-enrichment", queued)
    return queued
