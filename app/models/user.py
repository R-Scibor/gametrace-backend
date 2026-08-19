import hashlib
import secrets
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    discord_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Not unique: identity is discord_id. Discord usernames are user-renameable,
    # so a rename can collide with another account; indexed for the dev-login lookup.
    username: Mapped[str] = mapped_column(String(100), index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    language: Mapped[str] = mapped_column(
        String(8), default="pl", server_default="pl", nullable=False
    )
    weekly_report_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    push_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    auth_tokens: Mapped[list["UserAuthToken"]] = relationship(back_populates="user")
    devices: Mapped[list["UserDevice"]] = relationship(back_populates="user")


class UserAuthToken(Base):
    __tablename__ = "user_auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.discord_id", ondelete="CASCADE"))
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_active: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="auth_tokens")

    @staticmethod
    def generate_token() -> str:
        """Return a new raw token to hand to the client; never stored as-is."""
        return secrets.token_hex(32)

    @staticmethod
    def hash_token(raw: str) -> str:
        """SHA-256 hex of a raw token — the value stored in and looked up from the
        `token` column, so a DB dump never exposes usable session tokens. The token
        is high-entropy (256-bit), so an unsalted digest is sufficient here."""
        return hashlib.sha256(raw.encode()).hexdigest()


class UserDevice(Base):
    __tablename__ = "user_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.discord_id", ondelete="CASCADE"), index=True
    )
    fcm_token: Mapped[str] = mapped_column(String(512), unique=True)
    device_type: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_active: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="devices")
