"""
Celery task: enrich a game record with metadata from RAWG (primary) and Steam API (fallback).

Strategy:
1. RAWG fuzzy name search → confidence score via SequenceMatcher.
   - Score >85% → ENRICHED.
2. If RAWG score ≤85% → Steam store search (exact name match → 100% confidence) → ENRICHED.
3. Neither matched → NEEDS_REVIEW.

Exponential backoff on HTTP 429: 2^retry * 60s countdown.
Redis deduplication: task_id=f"enrich_game_{game_id}" ensures only one task per game is queued.
Custom covers: cover_image_url is NOT updated when cover_source=CUSTOM.
"""
import asyncio
import difflib
import logging

import httpx
from celery.exceptions import MaxRetriesExceededError

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.game import CoverSource, EnrichmentStatus, Game

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# External API helpers (sync — runs inside Celery worker process)
# ---------------------------------------------------------------------------

def _confidence(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _rawg_search(name: str) -> tuple[str | None, float]:
    """
    Search RAWG for *name*.
    Returns (cover_url_or_None, best_confidence).
    Raises httpx.HTTPStatusError on HTTP errors so caller can handle 429.
    """
    if not settings.rawg_api_key:
        logger.warning("RAWG_API_KEY not set — skipping RAWG search")
        return None, 0.0

    with httpx.Client(timeout=10) as client:
        resp = client.get(
            "https://api.rawg.io/api/games",
            params={"search": name, "key": settings.rawg_api_key, "page_size": 5},
        )
        resp.raise_for_status()

    results = resp.json().get("results", [])
    if not results:
        return None, 0.0

    best_score = 0.0
    best_cover: str | None = None
    for game in results:
        score = _confidence(name, game.get("name", ""))
        if score > best_score:
            best_score = score
            best_cover = game.get("background_image") or None

    return best_cover, best_score


def _steam_search(name: str) -> tuple[str | None, str | None]:
    """
    Search Steam store by name (exact match only).
    Returns (app_id, cover_url) or (None, None).
    Raises httpx.HTTPStatusError on HTTP errors.
    """
    with httpx.Client(timeout=10) as client:
        resp = client.get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": name, "l": "english", "cc": "US"},
        )
        resp.raise_for_status()

    norm = name.lower()
    for item in resp.json().get("items", []):
        if item.get("name", "").lower() == norm:
            app_id = str(item["id"])
            cover = f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg"
            return app_id, cover

    return None, None


# ---------------------------------------------------------------------------
# Async DB helpers
# ---------------------------------------------------------------------------

async def _get_game(game_id: int) -> Game | None:
    async with AsyncSessionLocal() as db:
        return await db.get(Game, game_id)


async def _save_enrichment(
    game_id: int,
    status: EnrichmentStatus,
    cover_url: str | None,
    external_api_id: str | None,
) -> None:
    async with AsyncSessionLocal() as db:
        game = await db.get(Game, game_id)
        if game is None:
            return
        game.enrichment_status = status
        if external_api_id is not None:
            game.external_api_id = external_api_id
        # Never overwrite a user-uploaded cover
        if game.cover_source != CoverSource.CUSTOM and cover_url is not None:
            game.cover_image_url = cover_url
        await db.commit()


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@celery_app.task(name="tasks.enrich_game", bind=True, max_retries=5)
def enrich_game(self, game_id: int) -> None:
    """
    Enrich game metadata.  Idempotent — safe to retry.
    """
    try:
        game = asyncio.run(_get_game(game_id))
        if game is None:
            logger.warning("enrich_game: game_id=%d not found in DB", game_id)
            return

        process_name: str = game.primary_name

        # ── RAWG primary ────────────────────────────────────────────────────
        rawg_cover: str | None = None
        rawg_confidence = 0.0
        try:
            rawg_cover, rawg_confidence = _rawg_search(process_name)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                countdown = (2 ** self.request.retries) * 60
                logger.warning(
                    "enrich_game: RAWG 429 for game_id=%d, retrying in %ds",
                    game_id, countdown,
                )
                raise self.retry(exc=exc, countdown=countdown)
            logger.warning("enrich_game: RAWG HTTP error %s for game_id=%d", exc, game_id)
        except Exception:
            logger.exception("enrich_game: RAWG lookup failed for game_id=%d", game_id)

        if rawg_confidence >= CONFIDENCE_THRESHOLD:
            asyncio.run(_save_enrichment(game_id, EnrichmentStatus.ENRICHED, rawg_cover, None))
            logger.info(
                "enrich_game: game_id=%d ENRICHED via RAWG (confidence=%.2f)",
                game_id, rawg_confidence,
            )
            return

        # ── Steam fallback ──────────────────────────────────────────────────
        try:
            steam_id, steam_cover = _steam_search(process_name)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                countdown = (2 ** self.request.retries) * 60
                logger.warning(
                    "enrich_game: Steam 429 for game_id=%d, retrying in %ds",
                    game_id, countdown,
                )
                raise self.retry(exc=exc, countdown=countdown)
            logger.warning("enrich_game: Steam HTTP error %s for game_id=%d", exc, game_id)
            steam_id, steam_cover = None, None
        except Exception:
            logger.exception("enrich_game: Steam lookup failed for game_id=%d", game_id)
            steam_id, steam_cover = None, None

        if steam_id is not None:
            asyncio.run(_save_enrichment(game_id, EnrichmentStatus.ENRICHED, steam_cover, steam_id))
            logger.info(
                "enrich_game: game_id=%d ENRICHED via Steam (app_id=%s)",
                game_id, steam_id,
            )
            return

        # ── No match ────────────────────────────────────────────────────────
        asyncio.run(_save_enrichment(game_id, EnrichmentStatus.NEEDS_REVIEW, None, None))
        logger.info(
            "enrich_game: game_id=%d → NEEDS_REVIEW "
            "(RAWG confidence=%.2f, no Steam exact match)",
            game_id, rawg_confidence,
        )

    except MaxRetriesExceededError:
        asyncio.run(_save_enrichment(game_id, EnrichmentStatus.NEEDS_REVIEW, None, None))
        logger.error("enrich_game: game_id=%d max retries exceeded → NEEDS_REVIEW", game_id)
    except Exception:
        logger.exception("enrich_game: unexpected error for game_id=%d", game_id)
        asyncio.run(_save_enrichment(game_id, EnrichmentStatus.NEEDS_REVIEW, None, None))
