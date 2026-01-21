"""Route history model for storing participant GPS tracking data."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.event import Event
    from app.models.user import UserAccount


class RouteHistory(Base):
    """
    Stores compressed route history for event participants.

    This replaces Firebase Firestore storage for route histories.
    Routes are synced when a user stops tracking, storing compressed
    GPS points and statistics for later analysis.
    """

    __tablename__ = "route_histories"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # User info (denormalized for quick access)
    display_name: Mapped[str] = mapped_column(nullable=False)

    # Tracking period
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    ended_at: Mapped[datetime] = mapped_column(nullable=False)

    # Route statistics
    total_distance_km: Mapped[float] = mapped_column(default=0.0)
    average_speed_kmh: Mapped[float] = mapped_column(default=0.0)
    max_speed_kmh: Mapped[float] = mapped_column(default=0.0)
    total_time_minutes: Mapped[int] = mapped_column(default=0)

    # Geofence compliance
    geofence_violations: Mapped[int] = mapped_column(default=0)
    time_outside_geofence_minutes: Mapped[int] = mapped_column(default=0)

    # Compressed route points (JSONB array)
    # Each point: { lat, lng, t (seconds from start), a (accuracy) }
    point_count: Mapped[int] = mapped_column(default=0)
    points: Mapped[dict] = mapped_column(JSONB, default=list)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # Relationships
    event: Mapped["Event"] = relationship("Event", lazy="selectin")
    user: Mapped["UserAccount"] = relationship("UserAccount", lazy="selectin")

    __table_args__ = (
        # Ensure one route per user per event (can be updated if they track again)
        Index("ix_route_histories_event_user", "event_id", "user_id", unique=True),
    )

    def __repr__(self) -> str:
        return f"<RouteHistory(id={self.id}, event_id={self.event_id}, user_id={self.user_id})>"
