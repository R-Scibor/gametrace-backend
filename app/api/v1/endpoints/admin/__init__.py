from fastapi import APIRouter

router = APIRouter()

from . import catalog, games, reports, stats  # noqa: E402, F401

router.include_router(stats.router)
router.include_router(reports.router)
router.include_router(catalog.router)
router.include_router(games.router)