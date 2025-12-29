"""Notification models: Notification, UserNotificationPreferences, UserDeviceToken."""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DeviceType:
    """Device type constants."""
    IOS = "ios"
    ANDROID = "android"
    WEB = "web"


class CatchNotificationLevel:
    """Catch notification level constants."""
    ALL = "all"      # Notify about all catches in events I'm participating in
    MINE = "mine"    # Only notify about my own catches
    NONE = "none"    # No catch notifications


class Notification(Base):
    """In-app notification model."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Notification content
    type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Additional data as JSONB (e.g., event_id, catch_id, etc.)
    data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Read status
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")

    def __repr__(self) -> str:
        return f"<Notification(id={self.id}, type={self.type}, is_read={self.is_read})>"


class UserNotificationPreferences(Base):
    """User notification preferences for event discovery."""

    __tablename__ = "user_notification_preferences"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Event discovery preferences
    notify_events_from_country: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    notify_event_types: Mapped[List[int]] = mapped_column(
        JSONB, default=list, nullable=False
    )  # empty = all event types
    notify_from_clubs: Mapped[List[int]] = mapped_column(
        JSONB, default=list, nullable=False
    )  # club IDs to follow

    # Event participation preferences (during ongoing events)
    notify_event_catches: Mapped[str] = mapped_column(
        String(20), default="all", nullable=False
    )  # 'all', 'mine', 'none' - catches in events I'm participating in

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship("UserAccount", back_populates="notification_preferences")

    def __repr__(self) -> str:
        return f"<UserNotificationPreferences(user_id={self.user_id})>"


class UserDeviceToken(Base):
    """User device tokens for push notifications."""

    __tablename__ = "user_device_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    device_type: Mapped[str] = mapped_column(String(20), nullable=False)  # ios, android, web

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship("UserAccount", back_populates="device_tokens")

    def __repr__(self) -> str:
        return f"<UserDeviceToken(user_id={self.user_id}, device_type={self.device_type})>"


# Import for type hints
from app.models.user import UserAccount
