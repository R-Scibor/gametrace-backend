from typing import Optional
from zoneinfo import available_timezones

from pydantic import BaseModel, field_validator


_VALID_TZS = available_timezones()


class ProfileResponse(BaseModel):
    discord_id: str
    username: str
    timezone: str
    weekly_report_enabled: bool
    push_enabled: bool


class ProfileSettingsUpdate(BaseModel):
    timezone: Optional[str] = None
    weekly_report_enabled: Optional[bool] = None
    push_enabled: Optional[bool] = None

    @field_validator("timezone")
    @classmethod
    def _tz_must_be_iana(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in _VALID_TZS:
            raise ValueError(f"Invalid IANA timezone: {v}")
        return v
