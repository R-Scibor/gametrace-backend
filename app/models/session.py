import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.game import Game
    from app.models.user import User


class SessionStatus(enum.StrEnum):
    ONGOING = "ONGOING"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class SessionSource(enum.StrEnum):
    BOT = "BOT"
    MANUAL = "MANUAL"


class GameSession(Base):
    __tablename__ = "game_sessions"
    __table_args__ = (
        Index(
            "uq_game_sessions_user_ongoing",
            "user_id",
            unique=True,
            postgresql_where="status = 'ONGOING' AND deleted_at IS NULL",
        ),
    )

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
    is_flicker: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship()
    game: Mapped["Game"] = relationship()
