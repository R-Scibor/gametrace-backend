from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VoiceUsage(Base):
    """One metadata row per PAID /voice/transcribe call — written as soon as
    Whisper returns, so calls whose Gemini parse later fails are recorded too
    (they cost real money). Such rows keep game_resolved=False and
    fields_extracted=0. Rejected uploads and Whisper failures write nothing.

    Cost/usage analytics, and the source the daily voice quota counts from
    (app/services/voice_quota.py) — never stores transcript text."""

    __tablename__ = "voice_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.discord_id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    audio_seconds: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    game_resolved: Mapped[bool] = mapped_column(Boolean)
    fields_extracted: Mapped[int] = mapped_column(Integer)