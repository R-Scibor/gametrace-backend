from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import require_admin
from app.core.database import get_db
from app.models.user import User
from app.schemas.admin import AdminOverviewResponse
from app.services import stats as stats_service

router = APIRouter()


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