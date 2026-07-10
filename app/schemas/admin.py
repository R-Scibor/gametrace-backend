from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AdminOverviewResponse(BaseModel):
    """Homelab-wide aggregate totals for the admin panel hub tiles."""

    user_count: int
    session_count: int
    total_seconds: int
    game_count: int
    needs_review_count: int
    pending_enrichment_count: int
    open_reports_count: int


class AdminReportItem(BaseModel):
    """A single row in the admin reports inbox."""

    id: int
    user_id: str
    username: str | None
    message: str
    context: dict
    status: str
    created_at: datetime


class AdminReportListResponse(BaseModel):
    """Paginated admin reports inbox listing."""

    total: int
    items: list[AdminReportItem]


class AdminReportPatch(BaseModel):
    """Triage update for a single report. No reopen in v1."""

    status: Literal["triaged", "closed"]
