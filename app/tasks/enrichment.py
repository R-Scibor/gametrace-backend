"""
Celery task: enrich a game record with metadata from an external API (IGDB/RAWG).
Phase 3 implementation — stub only for now.
"""
import logging

from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.enrich_game", bind=True, max_retries=5)
def enrich_game(self, game_id: int) -> None:
    """
    Fetch game metadata for the given game_id and update the games table.
    Implements Exponential Backoff on rate-limit errors (HTTP 429).
    Redis-based deduplication is handled at the call site via task ID.
    Phase 3: replace this stub with real IGDB/RAWG lookup + Confidence Scoring.
    """
    logger.info("enrich_game queued for game_id=%d (Phase 3 not yet implemented)", game_id)
