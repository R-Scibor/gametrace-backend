from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import require_admin
from app.core.database import get_db
from app.core.observability import log_admin_action
from app.models.report import Report
from app.models.user import User
from app.schemas.admin import (
    AdminReportFacet,
    AdminReportFacetsResponse,
    AdminReportItem,
    AdminReportListResponse,
    AdminReportPatch,
)

router = APIRouter()

_ESCAPE_CHAR = "\\"


def _escape_like(value: str) -> str:
    """Escape LIKE/ILIKE metacharacters so a typed `%`/`_` matches literally."""
    return (
        value.replace(_ESCAPE_CHAR, _ESCAPE_CHAR * 2)
        .replace("%", f"{_ESCAPE_CHAR}%")
        .replace("_", f"{_ESCAPE_CHAR}_")
    )


def _status_filter(status: str | None) -> list:
    return [Report.status == status] if status is not None else []


async def _facet_counts(db: AsyncSession, col, status_filter: list) -> list[AdminReportFacet]:
    """Distinct values + counts for a single `context` key, ordered count DESC, value ASC.

    Rows where `col` is SQL `NULL` (key absent or JSON `null`) are excluded so a
    missing key never surfaces as an empty-string facet.
    """
    result = await db.execute(
        select(col, func.count())
        .where(*status_filter, col.isnot(None))
        .group_by(col)
        .order_by(func.count().desc(), col.asc())
    )
    return [AdminReportFacet(value=value, count=count) for value, count in result.all()]


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
    q: str | None = Query(None, max_length=200),
    screen: str | None = None,
    platform: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),  # admin gate (router-level too; explicit for clarity)
):
    """Admin reports inbox: paginated, newest-first, filtered by status/q/screen/platform.

    Blank/whitespace-only `q`/`screen`/`platform` are treated as absent. `q` is
    a case-insensitive substring match on `message`; `%`/`_` are escaped so a
    typed literal matches literally rather than acting as SQL wildcards.
    """
    base_filter = _status_filter(status)

    q_stripped = q.strip() if q is not None else None
    if q_stripped:
        base_filter.append(
            Report.message.ilike(f"%{_escape_like(q_stripped)}%", escape=_ESCAPE_CHAR)
        )

    screen_stripped = screen.strip() if screen is not None else None
    if screen_stripped:
        base_filter.append(Report.context["screen"].astext == screen_stripped)

    platform_stripped = platform.strip() if platform is not None else None
    if platform_stripped:
        base_filter.append(Report.context["platform"].astext == platform_stripped)

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
# GET /reports/facets
# ---------------------------------------------------------------------------
# Registered before /reports/{report_id} below: a static path must win over a
# path-param route, or a literal "facets" id would get swallowed by PATCH/DELETE
# routing for {report_id} instead. Nothing shadows it today, but this ordering
# is the one-line guard against that footgun as more {report_id} routes land.

@router.get("/reports/facets", response_model=AdminReportFacetsResponse)
async def report_facets(
    status: Literal["open", "triaged", "closed"] | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),  # admin gate (router-level too; explicit for clarity)
):
    """Distinct `context.screen`/`context.platform` values + counts, for filter dropdowns.

    Scoped by `status` only — never by `q`/`screen`/`platform` — so picking a
    value from a dropdown never removes other options from that same dropdown.
    Reports whose `context` lacks the key (or has it as JSON `null`) are
    skipped rather than surfacing as an empty-string facet.
    """
    status_filter = _status_filter(status)

    screens = await _facet_counts(db, Report.context["screen"].astext, status_filter)
    platforms = await _facet_counts(db, Report.context["platform"].astext, status_filter)

    return AdminReportFacetsResponse(screens=screens, platforms=platforms)


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
    if "status" in fields_set and body.status is None:
        raise HTTPException(status_code=422, detail="status cannot be null.")

    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")

    status_changed = "status" in fields_set and body.status != report.status
    if status_changed:
        before_status = report.status
        report.status = body.status

    note_changed = False
    if "admin_note" in fields_set:
        new_note = (body.admin_note or "").strip() or None
        note_changed = new_note != report.admin_note
        if note_changed:
            before_marker = "set" if report.admin_note else "empty"
            after_marker = "set" if new_note else "empty"
            report.admin_note = new_note

    await db.commit()

    if status_changed:
        log_admin_action(
            user.discord_id,
            "report_triage",
            f"report:{report_id}",
            before=before_status,
            after=body.status,
        )
    if note_changed:
        log_admin_action(
            user.discord_id,
            "report_note",
            f"report:{report_id}",
            before=before_marker,
            after=after_marker,
        )

    result = await db.execute(
        select(Report, User.username)
        .outerjoin(User, Report.user_id == User.discord_id)
        .where(Report.id == report_id)
    )
    updated_report, username = result.one()

    return _to_report_item(updated_report, username)


# ---------------------------------------------------------------------------
# DELETE /reports/{report_id}
# ---------------------------------------------------------------------------

@router.delete("/reports/{report_id}", status_code=204)
async def delete_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),  # admin gate (router-level too; explicit for clarity)
):
    """Hard-delete a report. The row is gone; there is no soft-delete for reports."""
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")

    before_status = report.status
    message_preview = report.message[:80]

    await db.delete(report)
    await db.commit()

    log_admin_action(
        user.discord_id,
        "report_delete",
        f"report:{report_id}",
        before=before_status,
        detail=message_preview,
    )

    return None