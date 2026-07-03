from fastapi import APIRouter, Depends

from app.api.v1.endpoints import (
    admin,
    auth,
    games,
    health,
    notifications,
    preferences,
    profile,
    reports,
    sessions,
    stats,
    voice,
)
from app.api.v1.endpoints.auth import require_admin

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(
    admin.router,
    prefix="/admin",
    dependencies=[Depends(require_admin)],
    tags=["admin"],
)
api_router.include_router(games.router, prefix="/games", tags=["games"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(stats.router, prefix="/stats", tags=["stats"])
api_router.include_router(voice.router, prefix="/voice", tags=["voice"])
api_router.include_router(
    preferences.router, prefix="/user/preferences", tags=["preferences"]
)
api_router.include_router(profile.router, prefix="/profile", tags=["profile"])
api_router.include_router(
    notifications.router, prefix="/notifications", tags=["notifications"]
)
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
