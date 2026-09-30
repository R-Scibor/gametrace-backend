from zoneinfo import available_timezones

from pydantic import BaseModel, field_validator

_VALID_TZS = available_timezones()
_VALID_LANGUAGES = {"pl", "en"}


class ProfileResponse(BaseModel):
    discord_id: str
    username: str
    timezone: str
    language: str
    weekly_report_enabled: bool
    push_enabled: bool
    is_admin: bool


class ProfileSettingsUpdate(BaseModel):
    timezone: str | None = None
    language: str | None = None
    weekly_report_enabled: bool | None = None
    push_enabled: bool | None = None

    @field_validator("timezone", "language", "weekly_report_enabled", "push_enabled")
    @classmethod
    def _null_is_not_unset(cls, v: object) -> object:
        # None is the default for an omitted key. An explicit JSON null is
        # also None, and the columns are NOT NULL. Omit the key to leave it.
        if v is None:
            raise ValueError("null is not allowed; omit the field to leave it unchanged")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz_must_be_iana(cls, v: str) -> str:
        if v not in _VALID_TZS:
            raise ValueError(f"Invalid IANA timezone: {v}")
        return v

    @field_validator("language")
    @classmethod
    def _language_must_be_supported(cls, v: str) -> str:
        if v not in _VALID_LANGUAGES:
            raise ValueError(f"Unsupported language: {v}")
        return v
