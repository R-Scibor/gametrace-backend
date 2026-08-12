"""Pristine snapshot tables for the demo reviewer account.

`DemoSeedSession` / `DemoSeedPreference` hold a captured copy of the demo
user's `game_sessions` / `user_game_preferences` rows so a nightly job can
restore the demo account to a known-good state. Deliberately user-agnostic
(no `user_id` — the demo account is a singleton) and deliberately drop
`is_flicker` (an internal session-merge artifact irrelevant to a snapshot).
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DemoSeedSession(Base):
    __tablename__ = "demo_seed_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))


class DemoSeedPreference(Base):
    __tablename__ = "demo_seed_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    is_ignored: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    custom_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
