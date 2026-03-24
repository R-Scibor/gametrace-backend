import enum
from datetime import date, datetime

from sqlalchemy import DATE, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SessionStatus(str, enum.Enum):
    ONGOING = "ONGOING"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class SessionSource(str, enum.Enum):
    BOT = "BOT"
    MANUAL = "MANUAL"


class GameSession(Base):
    __tablename__ = "game_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.discord_id", ondelete="CASCADE"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[SessionStatus] = mapped_column(String(16))
    source: Mapped[SessionSource] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship()  # noqa: F821
    game: Mapped["Game"] = relationship()  # noqa: F821


class DailyUserStat(Base):
    __tablename__ = "daily_user_stats"
    __table_args__ = (UniqueConstraint("user_id", "game_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.discord_id", ondelete="CASCADE"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    date: Mapped[date] = mapped_column(DATE)
    total_seconds: Mapped[int] = mapped_column(Integer, default=0)
