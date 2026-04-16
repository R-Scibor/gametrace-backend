"""
Celery task: enrich a game record with metadata from IGDB (primary) and Steam API (fallback).

Strategy:
1. IGDB fuzzy name search → confidence score via rapidfuzz WRatio on sanitized names.
   Sanitization: lowercase, strip extensions/brackets, normalize roman numerals, collapse separators.
   alternative_names are also scored — best score across all candidate names is used.
   - Score >85% → ENRICHED (cover: vertical box art from t_cover_big).
2. If IGDB score ≤85% → Steam store search (exact name match → 100% confidence) → ENRICHED
   (cover: library_600x900.jpg — vertical portrait art).
3. Neither matched → NEEDS_REVIEW.

Exponential backoff on HTTP 429: 2^retry * 60s countdown.
Redis deduplication: task_id=f"enrich_game_{game_id}" ensures only one task per game is queued.
Custom covers: cover_image_url is NOT updated when cover_source=CUSTOM.

Event loop note: asyncpg connections are bound to the loop they were created on.
Reusing the global AsyncSessionLocal across multiple asyncio.run() calls causes
"Future attached to a different loop" errors. Fix: one asyncio.run() per task,
fresh engine created inside it, sync HTTP calls via asyncio.to_thread().
"""
import asyncio
import logging
import re

import httpx
from rapidfuzz import fuzz
from celery.exceptions import MaxRetriesExceededError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.game import CoverSource, EnrichmentStatus, Game
from app.tasks.igdb_auth import get_igdb_token, invalidate_igdb_token

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.85

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
    return ' '.join(tokens).strip()


def _confidence(a: str, b: str) -> float:
    return fuzz.WRatio(_sanitize(a), _sanitize(b)) / 100.0


def _igdb_search(name: str) -> tuple[str | None, float]:
    """Returns (cover_url, best_confidence). Raises _RateLimited on HTTP 429 or 401."""
    if not settings.igdb_client_id or not settings.igdb_client_secret:
        logger.warning("IGDB credentials not set — skipping IGDB search")
        return None, 0.0

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
            content=f'search "{safe_name}"; fields name,cover.url,cover.image_id,alternative_names.name; limit 5;',
        )

    if resp.status_code == 401:
        invalidate_igdb_token()
        raise _RateLimited("IGDB-auth")  # triggers Celery backoff retry

    if resp.status_code == 429:
        raise _RateLimited("IGDB")

    resp.raise_for_status()

    best_score = 0.0
    best_cover: str | None = None
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

    return best_cover, best_score


def _steam_search(name: str) -> tuple[str | None, str | None]:
    """Returns (app_id, cover_url) on exact match, else (None, None). Raises _RateLimited on 429."""
    with httpx.Client(timeout=10) as client:
        resp = client.get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": name, "l": "english", "cc": "US"},
        )

    if resp.status_code == 429:
        raise _RateLimited("Steam")
    resp.raise_for_status()

    norm = name.lower()
    for item in resp.json().get("items", []):
        if item.get("name", "").lower() == norm:
            app_id = str(item["id"])
            cover = f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/library_600x900.jpg"
            return app_id, cover

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
        igdb_cover: str | None = None
        igdb_confidence = 0.0
        try:
            igdb_cover, igdb_confidence = await asyncio.to_thread(_igdb_search, name)
        except _RateLimited:
            raise
        except Exception:
            logger.exception("enrich_game: IGDB lookup failed for game_id=%d", game_id)

        if igdb_confidence >= CONFIDENCE_THRESHOLD:
            async with SessionLocal() as db:
                await _apply(db, game_id, EnrichmentStatus.ENRICHED, igdb_cover, None)
            return EnrichmentStatus.ENRICHED, igdb_cover, None

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
) -> None:
    game = await db.get(Game, game_id)
    if game is None:
        return
    game.enrichment_status = status
    if external_api_id is not None:
        game.external_api_id = external_api_id
    if game.cover_source != CoverSource.CUSTOM and cover_url is not None:
        game.cover_image_url = cover_url
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
