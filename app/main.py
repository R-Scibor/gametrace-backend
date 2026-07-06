import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.observability import init_sentry

COVERS_DIR = os.environ.get("COVERS_DIR", "/app/covers")

init_sentry("api")

app = FastAPI(title="GameTrace API", version="1.0.0")

app.include_router(api_router, prefix="/api/v1")

# Serve custom game covers uploaded via PUT /api/v1/admin/games/{id}/cover
os.makedirs(COVERS_DIR, exist_ok=True)
app.mount("/covers", StaticFiles(directory=COVERS_DIR), name="covers")


@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    """Backstop cap on request body size, checked on Content-Length.

    The primary control is nginx client_max_body_size at the NPM edge, which
    enforces on the streamed (de-chunked) body against adversarial clients.
    This app-side check rejects the naive oversized upload early and is
    defense-in-depth if traffic ever reaches the container outside NPM.
    """
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            too_large = int(content_length) > settings.max_request_body_bytes
        except ValueError:
            too_large = False
        if too_large:
            return JSONResponse(
                status_code=413, content={"detail": "Request body too large."}
            )
    return await call_next(request)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
async def health():
    return {"status": "ok"}
