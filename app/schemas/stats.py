from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class CompanyRole(str, Enum):
    developer = "developer"
    publisher = "publisher"


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
    days: int                                      # 0 = all-time
    window_start: Optional[datetime] = None        # null in all-time mode (no lower bound)
    window_end: datetime
    total_seconds: int
    avg_session_seconds: int                       # mean COMPLETED session length in window
    longest_session_seconds: int                   # 0 when no COMPLETED sessions
    longest_session_game_id: Optional[int] = None
    longest_session_game_name: Optional[str] = None
    previous_total_seconds: int                    # COMPLETED total over the prior equal-length window
    new_games_count: int                           # games first-played within the window
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


class WeeklyTrendEntry(BaseModel):
    week_start: date          # Monday in user TZ
    total_seconds: int


class WeeklyTrendResponse(BaseModel):
    weeks: list[WeeklyTrendEntry]   # always `weeks` entries, oldest first


class GenreEntry(BaseModel):
    genre: str
    total_seconds: int


class GenresResponse(BaseModel):
    items: list[GenreEntry]


class ThemeEntry(BaseModel):
    theme: str
    total_seconds: int


class ThemesResponse(BaseModel):
    items: list[ThemeEntry]


class CompanyEntry(BaseModel):
    name: str
    total_seconds: int
    game_count: int


class CompaniesResponse(BaseModel):
    items: list[CompanyEntry]


class ReleaseYearEntry(BaseModel):
    decade: str          # e.g. "1990s", "2020s"
    total_seconds: int


class ReleaseYearsResponse(BaseModel):
    items: list[ReleaseYearEntry]   # sorted by decade asc


class GameStatsResponse(BaseModel):
    game_id: int
    total_seconds: int
    session_count: int
    first_played: datetime | None
    last_played: datetime | None
