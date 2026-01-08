"""Admin message model for platform contact form submissions."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AdminMessage(Base):
    """
    Messages sent by users to platform administrators via the Contact Us form.

    Stores a snapshot of sender info at the time of message submission
    to preserve the original contact details even if profile changes.
    """

    __tablename__ = "admin_messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Sender reference (nullable for non-authenticated visitors)
    sender_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Message content
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Sender info snapshot (preserved even if profile changes)
    sender_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sender_email: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Read status
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    read_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Relationships
    sender: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[sender_id], lazy="joined"
    )
    read_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[read_by_id], lazy="joined"
    )

    def __repr__(self) -> str:
        return f"<AdminMessage(id={self.id}, sender_id={self.sender_id}, subject={self.subject[:30]})>"


# Import for type hints
from app.models.user import UserAccount
