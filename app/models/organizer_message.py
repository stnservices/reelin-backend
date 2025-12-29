"""Organizer message model for contact form submissions."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class OrganizerMessage(Base):
    """
    Messages sent by users to event organizers via the contact form.

    Stores a snapshot of sender info at the time of message submission
    to preserve the original contact details even if profile changes.
    """

    __tablename__ = "organizer_messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Event and sender references
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Message content
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Sender info snapshot (preserved even if profile changes)
    sender_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sender_email: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_enrolled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Read status
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", lazy="joined")
    sender: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")

    def __repr__(self) -> str:
        return f"<OrganizerMessage(id={self.id}, event_id={self.event_id}, subject={self.subject[:30]})>"


# Import for type hints
from app.models.event import Event
from app.models.user import UserAccount
