import enum
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CoverSource(str, enum.Enum):
    EXTERNAL = "EXTERNAL"
    CUSTOM = "CUSTOM"


class EnrichmentStatus(str, enum.Enum):
    PENDING = "PENDING"
    ENRICHED = "ENRICHED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)
    primary_name: Mapped[str] = mapped_column(String(256))
    external_api_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cover_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cover_source: Mapped[CoverSource] = mapped_column(
        String(16), default=CoverSource.EXTERNAL, server_default=CoverSource.EXTERNAL
    )
    enrichment_status: Mapped[EnrichmentStatus] = mapped_column(
        String(16), default=EnrichmentStatus.PENDING, server_default=EnrichmentStatus.PENDING
    )
    first_release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    genres: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    themes: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    developers: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    publishers: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )

    aliases: Mapped[list["GameAlias"]] = relationship(back_populates="game")
    user_preferences: Mapped[list["UserGamePreference"]] = relationship(back_populates="game")


class GameAlias(Base):
    __tablename__ = "game_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    discord_process_name: Mapped[str] = mapped_column(String(256), unique=True, index=True)

    game: Mapped["Game"] = relationship(back_populates="aliases")


class UserGamePreference(Base):
    __tablename__ = "user_game_preferences"
    __table_args__ = (UniqueConstraint("user_id", "game_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.discord_id", ondelete="CASCADE"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    is_ignored: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    custom_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)

    game: Mapped["Game"] = relationship(back_populates="user_preferences")
