"""Event enrollment model."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EnrollmentStatus(str, Enum):
    """Enrollment status enum."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    DISQUALIFIED = "disqualified"


class EventEnrollment(Base):
    """Event enrollment model - tracks user registrations for events."""

    __tablename__ = "event_enrollments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(20), default=EnrollmentStatus.PENDING.value, nullable=False, index=True
    )

    # Draw number for seating/position assignment
    draw_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Approval tracking
    approved_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Disqualification tracking
    disqualified_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    disqualified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    disqualification_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Reinstatement tracking (if disqualification is reversed)
    reinstated_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    reinstated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reinstatement_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Timestamps
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="enrollments")
    user: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[user_id], lazy="joined"
    )
    approved_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[approved_by_id], lazy="joined"
    )
    disqualified_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[disqualified_by_id], lazy="joined"
    )
    reinstated_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[reinstated_by_id], lazy="joined"
    )
    team_membership: Mapped[Optional["TeamMember"]] = relationship(
        "TeamMember", back_populates="enrollment", uselist=False,
        cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_pending(self) -> bool:
        return self.status == EnrollmentStatus.PENDING.value

    @property
    def is_approved(self) -> bool:
        return self.status == EnrollmentStatus.APPROVED.value

    @property
    def is_rejected(self) -> bool:
        return self.status == EnrollmentStatus.REJECTED.value

    @property
    def is_disqualified(self) -> bool:
        return self.status == EnrollmentStatus.DISQUALIFIED.value

    def __repr__(self) -> str:
        return f"<EventEnrollment(id={self.id}, event_id={self.event_id}, user_id={self.user_id}, status={self.status})>"


class EventBan(Base):
    """Event ban model - tracks users banned from specific events."""

    __tablename__ = "event_bans"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    banned_by_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    banned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", lazy="joined")
    user: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[user_id], lazy="joined"
    )
    banned_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[banned_by_id], lazy="joined"
    )

    def __repr__(self) -> str:
        return f"<EventBan(id={self.id}, event_id={self.event_id}, user_id={self.user_id})>"


# Import for type hints
from app.models.event import Event
from app.models.user import UserAccount
from app.models.team import TeamMember
