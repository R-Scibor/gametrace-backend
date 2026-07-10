from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.game import CoverSource, EnrichmentStatus


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


class AdminGameItem(BaseModel):
    """A single row in the admin global catalog list."""

    id: int
    primary_name: str
    enrichment_status: EnrichmentStatus
    cover_image_url: str | None
    cover_source: CoverSource
    external_api_id: str | None
    aliases: list[str]
    session_count: int


class AdminGameListResponse(BaseModel):
    """Paginated admin global catalog listing."""

    total: int
    items: list[AdminGameItem]


class IgdbLinkRequest(BaseModel):
    """Link an existing catalog row to a specific IGDB game id."""

    igdb_id: int
