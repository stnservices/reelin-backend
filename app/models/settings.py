"""Platform settings models for configurable options."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class VideoDurationOption(Base):
    """Video duration options for event media settings.

    Stores configurable video duration limits that appear in event creation/edit forms.
    Managed by administrators through the settings panel.
    """

    __tablename__ = "video_duration_options"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    seconds: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g., "3 seconds"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    def __repr__(self) -> str:
        return f"<VideoDurationOption(id={self.id}, seconds={self.seconds}, label={self.label})>"
