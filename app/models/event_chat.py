"""Event chat models for real-time messaging within events."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MessageType:
    """Message type constants."""
    MESSAGE = "message"          # Regular chat message
    ANNOUNCEMENT = "announcement"  # Highlighted organizer announcement
    SYSTEM = "system"            # System-generated message (user joined, etc.)


class EventChatMessage(Base):
    """Event chat message model for participant communication."""

    __tablename__ = "event_chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Event and user references
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Message content
    message: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(
        String(20),
        default=MessageType.MESSAGE,
        nullable=False
    )

    # Organizer features - pinned messages
    is_pinned: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True
    )
    pinned_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"),
        nullable=True
    )
    pinned_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True
    )
    deleted_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"),
        nullable=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", lazy="joined")
    user: Mapped["UserAccount"] = relationship(
        "UserAccount",
        foreign_keys=[user_id],
        lazy="joined"
    )
    pinned_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount",
        foreign_keys=[pinned_by_id],
        lazy="joined"
    )
    deleted_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount",
        foreign_keys=[deleted_by_id],
        lazy="joined"
    )

    def __repr__(self) -> str:
        return f"<EventChatMessage(id={self.id}, event_id={self.event_id}, user_id={self.user_id})>"


# Import for type hints
from app.models.event import Event
from app.models.user import UserAccount
