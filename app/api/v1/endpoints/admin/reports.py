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
    """Triage a single report: any status may transition to any other.

    Partial update over two independent columns (`status`, `admin_note`).
    Only keys present in the request body are written; an empty body (no
    recognized key) is rejected. `admin_note` is trimmed server-side, and
    both an explicit `null` and a trimmed-empty string clear it to `NULL`.
    A field set to the value it already holds is a no-op: `200` is returned
    but no audit line is emitted for that field.
    """
    fields_set = body.model_fields_set
    if "status" not in fields_set and "admin_note" not in fields_set:
        raise HTTPException(status_code=422, detail="No fields to update.")

    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")

    if "status" in fields_set and body.status != report.status:
        before_status = report.status
        report.status = body.status
        log_admin_action(
            user.discord_id,
            "report_triage",
            f"report:{report_id}",
            before=before_status,
            after=body.status,
        )

    if "admin_note" in fields_set:
        new_note = (body.admin_note or "").strip() or None
        if new_note != report.admin_note:
            before_marker = "set" if report.admin_note else "empty"
            after_marker = "set" if new_note else "empty"
            report.admin_note = new_note
            log_admin_action(
                user.discord_id,
                "report_note",
                f"report:{report_id}",
                before=before_marker,
                after=after_marker,
            )

    await db.commit()

    result = await db.execute(
        select(Report, User.username)
        .outerjoin(User, Report.user_id == User.discord_id)
        .where(Report.id == report_id)
    )
    updated_report, username = result.one()

    return _to_report_item(updated_report, username)