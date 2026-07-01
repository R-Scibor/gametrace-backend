from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.report import Report
from app.models.user import User
from app.schemas.report import ReportCreate, ReportResponse

router = APIRouter()


@router.post("", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
async def create_report(
    payload: ReportCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Persist a user feedback report (free-text message + diagnostic context)."""
    report = Report(
        user_id=user.discord_id,
        message=payload.message,
        context=payload.context.model_dump(by_alias=True),
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return ReportResponse(id=report.id, created_at=report.created_at)
