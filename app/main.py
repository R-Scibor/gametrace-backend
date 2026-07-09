import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, new_trace_id
from app.core.observability import init_sentry
from app.core.rate_limit import limiter

COVERS_DIR = os.environ.get("COVERS_DIR", "/app/covers")

configure_logging(settings.log_component or "api", settings.log_level)
init_sentry("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # uvicorn installs its own logging config on server startup (we boot it
    # without --log-config), so re-assert ours here to win the ordering.
    configure_logging(settings.log_component or "api", settings.log_level)
    yield


# Swagger/ReDoc/openapi.json document every route (including the dev-login
# header, defeating its 404 masking) — keep them off on exposed deployments.
app = FastAPI(
    title="GameTrace API",
    version="1.0.0",
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url="/redoc" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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


@app.middleware("http")
async def bind_request_id(request: Request, call_next):
    """Bind a request_id into log context and echo it back as a header."""
    request_id = request.headers.get("x-request-id") or new_trace_id()
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        import sentry_sdk

        sentry_sdk.set_tag("request_id", request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        structlog.contextvars.clear_contextvars()


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
async def health():
    return {"status": "ok"}
