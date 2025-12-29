"""App settings model for dynamic configuration."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AppSettings(Base):
    """
    Application settings stored in database.

    This is a single-row table that stores app-wide configuration
    that can be updated by admins without redeploying.
    """

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # App version settings
    app_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)
    app_min_version_ios: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)
    app_min_version_android: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)

    # Store URLs
    app_store_url: Mapped[str] = mapped_column(
        String(500),
        default="https://apps.apple.com/app/reelin/id123456789",
        nullable=False
    )
    play_store_url: Mapped[str] = mapped_column(
        String(500),
        default="https://play.google.com/store/apps/details?id=ro.reelin.app",
        nullable=False
    )

    # Release notes (optional, shown in update dialog)
    release_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Force update message (optional, shown when update is required)
    force_update_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    updated_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<AppSettings(app_version={self.app_version})>"
