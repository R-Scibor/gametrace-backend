from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

EVENT_REQUESTED = "requested"
EVENT_CANCELLED = "cancelled"
EVENT_PURGED = "purged"

_VALID_EVENTS = (EVENT_REQUESTED, EVENT_CANCELLED, EVENT_PURGED)


class AccountDeletionEvent(Base):
    """Append-only Art. 17 trail. No FK to users — must outlive hard purge."""

    __tablename__ = "account_deletion_events"
    __table_args__ = (
        CheckConstraint(
            f"event IN ('{EVENT_REQUESTED}', '{EVENT_CANCELLED}', '{EVENT_PURGED}')",
            name="ck_account_deletion_events_event",
        ),
        Index(
            "ix_account_deletion_events_discord_id_created_at",
            "discord_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    discord_id: Mapped[str] = mapped_column(String(32), nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    purge_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


def record_deletion_event(
    db: AsyncSession, discord_id: str, event: str, purge_at: datetime | None = None
) -> None:
    """Stage an audit row on `db` (caller commits). Shared by schedule_deletion,
    cancel_deletion, and the purge sweep so the row shape only lives in one
    place."""
    db.add(AccountDeletionEvent(discord_id=discord_id, event=event, purge_at=purge_at))
