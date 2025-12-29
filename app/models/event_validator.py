"""EventValidator junction table for many-to-many event-validator relationship."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.event import Event
    from app.models.user import UserAccount


class EventValidator(Base):
    """
    Junction table for event-validator many-to-many relationship.

    Allows multiple validators to be assigned to a single event.
    Tracks who assigned the validator and when.
    """

    __tablename__ = "event_validators"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    validator_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Unique constraint: one user can only be validator once per event
    __table_args__ = (
        UniqueConstraint("event_id", "validator_id", name="uq_event_validator"),
    )

    # Relationships
    event: Mapped["Event"] = relationship(
        "Event", back_populates="validators", lazy="joined"
    )
    validator: Mapped["UserAccount"] = relationship(
        "UserAccount",
        foreign_keys=[validator_id],
        lazy="joined"
    )
    assigned_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount",
        foreign_keys=[assigned_by_id],
        lazy="joined"
    )

    def __repr__(self) -> str:
        return f"<EventValidator(event_id={self.event_id}, validator_id={self.validator_id})>"
