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
