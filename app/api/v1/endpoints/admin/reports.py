from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import require_admin
from app.core.database import get_db
from app.core.observability import log_admin_action
from app.models.report import Report
from app.models.user import User
from app.schemas.admin import AdminReportItem, AdminReportListResponse, AdminReportPatch

router = APIRouter()


def _to_report_item(report: Report, username: str | None) -> AdminReportItem:
    return AdminReportItem(
        id=report.id,
        user_id=report.user_id,
        username=username,
        message=report.message,
        context=report.context,
        status=report.status,
        created_at=report.created_at,
        admin_note=report.admin_note,
    )


# ---------------------------------------------------------------------------
# GET /reports
# ---------------------------------------------------------------------------

@router.get("/reports", response_model=AdminReportListResponse)
async def list_reports(
    status: Literal["open", "triaged", "closed"] | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),  # admin gate (router-level too; explicit for clarity)
):
    """Admin reports inbox: paginated, newest-first, optionally filtered by status."""
    base_filter = [Report.status == status] if status is not None else []

    total = await db.scalar(
        select(func.count()).select_from(Report).where(*base_filter)
    )

    result = await db.execute(
        select(Report, User.username)
        .outerjoin(User, Report.user_id == User.discord_id)
        .where(*base_filter)
        .order_by(Report.created_at.desc(), Report.id.desc())
        .offset(skip)
        .limit(limit)
    )

    items = [
        _to_report_item(report, username)
        for report, username in result.all()
    ]

    return AdminReportListResponse(total=total, items=items)


# ---------------------------------------------------------------------------
# PATCH /reports/{report_id}
# ---------------------------------------------------------------------------

@router.patch("/reports/{report_id}", response_model=AdminReportItem)
async def triage_report(
    report_id: int,
    body: AdminReportPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),  # admin gate (router-level too; explicit for clarity)
):
    """Triage a single report: any status may transition to any other."""
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")

    before = report.status
    report.status = body.status
    await db.commit()

    log_admin_action(
        user.discord_id, "report_triage", f"report:{report_id}", before=before, after=body.status
    )

    result = await db.execute(
        select(Report, User.username)
        .outerjoin(User, Report.user_id == User.discord_id)
        .where(Report.id == report_id)
    )
    updated_report, username = result.one()

    return _to_report_item(updated_report, username)