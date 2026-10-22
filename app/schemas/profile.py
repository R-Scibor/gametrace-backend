from datetime import UTC, datetime, timedelta
from zoneinfo import available_timezones

from pydantic import BaseModel, field_validator

_VALID_TZS = available_timezones()
_VALID_LANGUAGES = {"pl", "en"}
# Upper bound on a reminder snooze; longer means "turn reminders off".
MAX_SNOOZE = timedelta(days=366)


class ProfileResponse(BaseModel):
    discord_id: str
    username: str
    timezone: str
    language: str
    weekly_report_enabled: bool
    push_enabled: bool
    is_admin: bool
    # None = unknown (bot offline, stale heartbeat, no fresh snapshot, Redis
    # error, demo account). Clients should remind only on an explicit False.
    bot_can_see_user: bool | None
    bot_visibility_reminders_enabled: bool
    bot_visibility_reminder_snoozed_until: datetime | None


class ProfileSettingsUpdate(BaseModel):
    timezone: str | None = None
    language: str | None = None
    weekly_report_enabled: bool | None = None
    push_enabled: bool | None = None
    bot_visibility_reminders_enabled: bool | None = None
    # Explicit null is allowed here: it clears the snooze (nullable column).
    bot_visibility_reminder_snoozed_until: datetime | None = None

    @field_validator(
        "timezone",
        "language",
        "weekly_report_enabled",
        "push_enabled",
        "bot_visibility_reminders_enabled",
    )
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

    @field_validator("bot_visibility_reminder_snoozed_until")
    @classmethod
    def _snooze_aware_utc_bounded(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("snooze datetime must include a timezone offset")
        v = v.astimezone(UTC)
        if v > datetime.now(UTC) + MAX_SNOOZE:
            raise ValueError("snooze may be at most 366 days in the future")
        return v
