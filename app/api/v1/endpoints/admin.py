import base64
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import require_admin
from app.core.database import get_db
from app.core.observability import log_admin_action
from app.models.game import CoverSource, Game, GameAlias, UserGamePreference
from app.models.session import GameSession
from app.models.user import User
from app.schemas.admin import AdminOverviewResponse
from app.schemas.game import CoverUpload, GameResponse
from app.services import stats as stats_service
from app.services.upload_validation import sniff_image_extension

router = APIRouter()

ALLOWED_COVER_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


# ---------------------------------------------------------------------------
# POST /games/{game_id}/merge/{target_id}
# ---------------------------------------------------------------------------

@router.post("/games/{game_id}/merge/{target_id}", status_code=204)
async def merge_game(
    game_id: int,
    target_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),  # admin gate + identity for audit log
):
    """
    Merge game_id into target_id (ACID transaction):
    1. Reassign all game_aliases → target_id
    2. Reassign all game_sessions → target_id
    3. Merge user_game_preferences (drop conflicts, reassign rest)
    4. Delete source game record

    Returns 204 No Content on success.
    Returns 404 if either game does not exist.
    Returns 400 if game_id == target_id.
    """
    if game_id == target_id:
        raise HTTPException(status_code=400, detail="Cannot merge a game into itself.")

    source = await db.get(Game, game_id)
    target = await db.get(Game, target_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found.")
    if target is None:
        raise HTTPException(status_code=404, detail=f"Game {target_id} not found.")

    # ── All operations in a single transaction ─────────────────────────────
    # 1. Reassign aliases (unique on discord_process_name — no conflicts possible)
    await db.execute(
        update(GameAlias).where(GameAlias.game_id == game_id).values(game_id=target_id)
    )

    # 2. Reassign sessions
    await db.execute(
        update(GameSession).where(GameSession.game_id == game_id).values(game_id=target_id)
    )

    # 3. UserGamePreference has UNIQUE (user_id, game_id).
    #    Drop source rows where target already has a preference for the same user.
    await db.execute(
        delete(UserGamePreference).where(
            UserGamePreference.game_id == game_id,
            UserGamePreference.user_id.in_(
                select(UserGamePreference.user_id).where(
                    UserGamePreference.game_id == target_id
                )
            ),
        )
    )
    await db.execute(
        update(UserGamePreference)
        .where(UserGamePreference.game_id == game_id)
        .values(game_id=target_id)
    )

    # 4. Delete the source game record
    await db.delete(source)
    await db.commit()

    log_admin_action(
        user.discord_id, "merge_game", f"game:{game_id}", after=f"target:{target_id}"
    )


# ---------------------------------------------------------------------------
# PUT /games/{game_id}/cover
# ---------------------------------------------------------------------------

@router.put("/games/{game_id}/cover", response_model=GameResponse)
async def upload_cover(
    game_id: int,
    body: CoverUpload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),  # admin gate + identity for audit log
):
    """
    Admin-only custom cover upload. Decodes `image_base64` and writes it to
    COVERS_DIR (env var, default /app/covers) as "{game_id}.{extension}",
    then stores a durable relative URL ("/covers/{game_id}.{extension}") on
    the Game row with cover_source=CUSTOM. The enrichment worker skips
    overwriting CUSTOM covers (see tasks/enrichment.py).

    Returns 422 for an unsupported/invalid extension or malformed base64.
    Returns 404 if the game does not exist.
    """
    extension = body.extension.lower()
    if extension not in ALLOWED_COVER_EXTENSIONS:
        raise HTTPException(
            status_code=422, detail=f"Unsupported cover extension: {body.extension!r}"
        )

    try:
        image_bytes = base64.b64decode(body.image_base64, validate=True)
    except Exception:
        raise HTTPException(status_code=422, detail="Malformed base64 image data.")

    # Second layer over the extension allowlist: the bytes must actually be an
    # image, so a mislabeled/polyglot file can't be stored and served same-origin.
    if sniff_image_extension(image_bytes) is None:
        raise HTTPException(
            status_code=422, detail="Uploaded data is not a supported image (jpeg/png/webp)."
        )

    game = await db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found.")

    before_url = game.cover_image_url
    before_source = CoverSource(game.cover_source).value

    covers_dir = os.environ.get("COVERS_DIR", "/app/covers")
    os.makedirs(covers_dir, exist_ok=True)
    file_path = os.path.join(covers_dir, f"{game_id}.{extension}")
    with open(file_path, "wb") as f:
        f.write(image_bytes)

    new_url = f"/covers/{game_id}.{extension}"
    game.cover_image_url = new_url
    game.cover_source = CoverSource.CUSTOM
    await db.commit()
    await db.refresh(game)

    log_admin_action(
        user.discord_id,
        "upload_cover",
        f"game:{game_id}",
        before=f"cover_image_url={before_url} cover_source={before_source}",
        after=f"cover_image_url={new_url} cover_source={CoverSource.CUSTOM.value}",
    )

    return game


# ---------------------------------------------------------------------------
# GET /stats/overview
# ---------------------------------------------------------------------------

@router.get("/stats/overview", response_model=AdminOverviewResponse)
async def stats_overview(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),  # admin gate (router-level too; explicit for clarity)
):
    """Homelab-wide aggregate totals for the admin panel hub (read-only)."""
    return await stats_service.admin_overview(db)
