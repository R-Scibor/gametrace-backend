from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
    admin_note: str | None


class AdminReportListResponse(BaseModel):
    """Paginated admin reports inbox listing."""

    total: int
    items: list[AdminReportItem]


class AdminReportFacet(BaseModel):
    """A single distinct value + count for a report context facet."""

    value: str
    count: int


class AdminReportFacetsResponse(BaseModel):
    """Distinct `context` values in the reports table, for filter dropdowns.

    Scoped by `status` only — never by `q`/`screen`/`platform` — so a dropdown
    always lists every value in the bucket, including ones not currently
    selected.
    """

    screens: list[AdminReportFacet]
    platforms: list[AdminReportFacet]


class AdminReportPatch(BaseModel):
    """Triage update for a single report. Any status may transition to any other.

    Partial update: only keys present in the request body are applied — check
    `model_fields_set`, not attribute truthiness, since a missing key and an
    explicit JSON `null` both collapse to `None` after Pydantic parses this.
    """

    status: Literal["open", "triaged", "closed"] | None = None
    admin_note: str | None = Field(default=None, max_length=4000)


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


class AliasCreateRequest(BaseModel):
    """Attach a Discord process-name alias to an existing catalog row."""

    discord_process_name: str


class AdminAliasResponse(BaseModel):
    """Alias attachment result."""

    game_id: int
    discord_process_name: str
