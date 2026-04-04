"""
One-shot re-enrichment script.

Resets all EXTERNAL-cover games to PENDING and re-enqueues Celery enrichment tasks.
Run this after deploying the IGDB swap to replace stale RAWG landscape covers.

Usage (inside the running worker container):
    docker compose run --rm worker python scripts/reset_enrichment.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.game import CoverSource, EnrichmentStatus, Game
from app.tasks.enrichment import enrich_game


async def main() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as db:
        result = await db.execute(
            select(Game.id).where(Game.cover_source == CoverSource.EXTERNAL)
        )
        game_ids = [row[0] for row in result.all()]

        await db.execute(
            update(Game)
            .where(Game.cover_source == CoverSource.EXTERNAL)
            .values(enrichment_status=EnrichmentStatus.PENDING, cover_image_url=None)
        )
        await db.commit()

    await engine.dispose()

    print(f"Reset {len(game_ids)} games to PENDING. Enqueuing enrichment tasks...")
    for game_id in game_ids:
        enrich_game.apply_async(
            args=[game_id],
            task_id=f"enrich_game_{game_id}",
        )

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
