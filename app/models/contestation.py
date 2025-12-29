"""Event contestation models for disputes and reports."""

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.catch import Catch
    from app.models.event import Event
    from app.models.user import UserAccount


class ContestationStatus(str, Enum):
    """Status of a contestation."""
    PENDING = "pending"
    APPROVED = "approved"  # Contestation was valid
    REJECTED = "rejected"  # Contestation was invalid/unfounded


class ContestationType(str, Enum):
    """Type of contestation being submitted."""
    RULE_VIOLATION = "rule_violation"
    CATCH_DISPUTE = "catch_dispute"
    UNSPORTSMANLIKE = "unsportsmanlike"
    OTHER = "other"


class EventContestation(Base):
    """
    Model for event contestations/disputes/reports.

    Participants can report rule violations, dispute catches, or report
    unsportsmanlike behavior during an event or within 1 hour after completion.
    Organizers can review and apply penalty points if the contestation is valid.
    """
    __tablename__ = "event_contestations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Event reference
    event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Who submitted the report
    reporter_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Who is being reported (optional - for general reports, this can be null)
    reported_user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("user_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Specific catch being disputed (optional)
    reported_catch_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("catches.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Contestation details
    contestation_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ContestationType.OTHER.value,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Status
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ContestationStatus.PENDING.value,
        index=True,
    )

    # Review fields
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("user_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    review_notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Penalty applied (points to deduct from reported user's score)
    penalty_points_applied: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="contestations")
    reporter: Mapped["UserAccount"] = relationship(
        "UserAccount",
        foreign_keys=[reporter_user_id],
        backref="reported_contestations",
    )
    reported_user: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount",
        foreign_keys=[reported_user_id],
        backref="contestations_against",
    )
    reported_catch: Mapped[Optional["Catch"]] = relationship(
        "Catch",
        foreign_keys=[reported_catch_id],
        backref="contestations",
    )
    reviewed_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount",
        foreign_keys=[reviewed_by_id],
        backref="reviewed_contestations",
    )

    def __repr__(self) -> str:
        return f"<EventContestation(id={self.id}, event_id={self.event_id}, type={self.contestation_type}, status={self.status})>"
