from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class GameStatEntry(BaseModel):
    game_id: int
    game_name: str
    cover_image_url: Optional[str] = None
    total_seconds: int


class PendingErrorEntry(BaseModel):
    id: int
    game_id: int
    game_name: str
    start_time: datetime
    notes: Optional[str] = None


class StatsSummaryResponse(BaseModel):
    days: int
    window_start: datetime
    window_end: datetime
    total_seconds: int
    per_game: list[GameStatEntry]
    pending_errors: list[PendingErrorEntry]


class ActiveSessionBrief(BaseModel):
    id: int
    game_id: int
    game_name: str
    cover_image_url: Optional[str] = None
    start_time: datetime


class DashboardResponse(BaseModel):
    total_seconds_today: int
    total_seconds_7d: int
    total_seconds_30d: int
    active_session: Optional[ActiveSessionBrief] = None
    pending_errors: list[PendingErrorEntry]


class HeatmapCell(BaseModel):
    dow: int    # 0=Monday, 6=Sunday
    hour: int   # 0..23
    seconds: int


class HeatmapResponse(BaseModel):
    days: int
    cells: list[HeatmapCell]   # always 168 entries


class StreakResponse(BaseModel):
    current_streak: int
    longest_streak: int
