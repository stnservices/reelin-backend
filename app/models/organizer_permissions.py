"""Organizer permission models for event type access and national events."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import UserAccount
    from app.models.event import EventType


class OrganizerEventTypeAccess(Base):
    """
    Tracks which organizers can create which event types.
    Admin grants access; organizers only see accessible types in creation wizard.
    """

    __tablename__ = "organizer_event_type_access"
    __table_args__ = (
        UniqueConstraint("user_id", "event_type_id", name="uq_organizer_event_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type_id: Mapped[int] = mapped_column(
        ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    granted_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    user: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[user_id], lazy="joined"
    )
    event_type: Mapped["EventType"] = relationship("EventType", lazy="joined")
    granted_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[granted_by_id], lazy="joined"
    )

    def __repr__(self) -> str:
        return f"<OrganizerEventTypeAccess(user_id={self.user_id}, event_type_id={self.event_type_id})>"


class NationalEventOrganizer(Base):
    """
    Whitelist of organizers authorized to create national events.
    Only admins can grant/revoke this permission.
    """

    __tablename__ = "national_event_organizers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    granted_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    user: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[user_id], lazy="joined"
    )
    granted_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[granted_by_id], lazy="joined"
    )

    def __repr__(self) -> str:
        return f"<NationalEventOrganizer(user_id={self.user_id})>"
