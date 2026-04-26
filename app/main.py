import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.observability import init_sentry

COVERS_DIR = os.environ.get("COVERS_DIR", "/app/covers")

init_sentry("api")

app = FastAPI(title="GameTrace API", version="1.0.0")

app.include_router(api_router, prefix="/api/v1")

# Serve custom game covers uploaded via PUT /games/{id}/cover
os.makedirs(COVERS_DIR, exist_ok=True)
app.mount("/covers", StaticFiles(directory=COVERS_DIR), name="covers")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
async def health():
    return {"status": "ok"}
