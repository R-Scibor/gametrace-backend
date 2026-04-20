from fastapi import APIRouter

from app.api.v1.endpoints import auth, games, preferences, sessions, stats, voice

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(games.router, prefix="/games", tags=["games"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(stats.router, prefix="/stats", tags=["stats"])
api_router.include_router(voice.router, prefix="/voice", tags=["voice"])
api_router.include_router(
    preferences.router, prefix="/user/preferences", tags=["preferences"]
)
