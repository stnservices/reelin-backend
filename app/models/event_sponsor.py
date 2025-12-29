"""Event-Sponsor junction table for many-to-many relationship."""

from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.event import Event
    from app.models.sponsor import Sponsor


class EventSponsor(Base):
    """
    Junction table for event-sponsor many-to-many relationship.
    An event can have multiple sponsors and a sponsor can sponsor multiple events.
    """

    __tablename__ = "event_sponsors"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sponsor_id: Mapped[int] = mapped_column(
        ForeignKey("sponsors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Unique constraint: one sponsor per event
    __table_args__ = (
        UniqueConstraint("event_id", "sponsor_id", name="uq_event_sponsor"),
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="event_sponsors")
    sponsor: Mapped["Sponsor"] = relationship("Sponsor", lazy="joined")

    def __repr__(self) -> str:
        return f"<EventSponsor(event_id={self.event_id}, sponsor_id={self.sponsor_id})>"
