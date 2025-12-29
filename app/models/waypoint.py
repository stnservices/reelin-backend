"""User waypoint models for saving fishing spots."""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class WaypointIcon(Base):
    """Admin-managed waypoint icon definitions."""

    __tablename__ = "waypoint_icons"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    emoji: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    svg_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_pro_only: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<WaypointIcon(code={self.code}, name={self.name})>"


class WaypointCategory(Base):
    """Admin-managed waypoint category definitions."""

    __tablename__ = "waypoint_categories"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str] = mapped_column(String(7), server_default="#E85D04", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)

    def __repr__(self) -> str:
        return f"<WaypointCategory(code={self.code}, name={self.name})>"


class UserWaypoint(Base):
    """User's saved waypoint (fishing spot)."""

    __tablename__ = "user_waypoints"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Owner
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Location
    latitude: Mapped[Decimal] = mapped_column(Numeric(10, 8), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(11, 8), nullable=False)

    # Details
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    icon: Mapped[str] = mapped_column(String(50), server_default="pin", nullable=False)
    color: Mapped[str] = mapped_column(String(7), server_default="#E85D04", nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Media (Pro only)
    photo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Sharing (Pro only)
    is_shared: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    shared_with: Mapped[List[int]] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "user_id", "latitude", "longitude", name="uq_user_waypoints_location"
        ),
    )

    def __repr__(self) -> str:
        return f"<UserWaypoint(id={self.id}, name={self.name}, user_id={self.user_id})>"


# Import for type hints
from app.models.user import UserAccount
