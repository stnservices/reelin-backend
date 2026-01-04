"""Statistics models: UserEventTypeStats for aggregated user statistics."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserEventTypeStats(Base):
    """
    Aggregated user statistics per event type.
    Tracks lifetime statistics for achievements display.
    Null event_type_id means overall statistics across all event types.
    """

    __tablename__ = "user_event_type_stats"
    __table_args__ = (
        UniqueConstraint("user_id", "event_type_id", name="uq_user_event_type_stats"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Event type (null = overall stats across all types)
    event_type_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("event_types.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Participation stats
    total_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_events_this_year: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Catch stats
    total_catches: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_approved_catches: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_rejected_catches: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Ranking stats
    total_wins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 1st place finishes
    podium_finishes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # Top 3 finishes
    best_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Best ever rank

    # Points stats
    total_points: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # Lifetime points
    total_bonus_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_penalty_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Catch quality stats
    largest_catch_cm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    largest_catch_species_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("fish.id", ondelete="SET NULL"), nullable=True
    )
    average_catch_length: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Species diversity
    unique_species_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Streak tracking (current consecutive events participated)
    consecutive_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_consecutive_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Last activity
    last_event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    last_event_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ==========================================================================
    # TA-specific stats (nullable - only populated for TA participants)
    # ==========================================================================
    ta_total_matches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ta_match_wins: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ta_match_losses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ta_match_ties: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ta_total_catches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ta_tournament_wins: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ta_tournament_podiums: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ==========================================================================
    # TSF-specific stats (nullable - only populated for TSF participants)
    # ==========================================================================
    tsf_total_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tsf_sector_wins: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tsf_total_catches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tsf_tournament_wins: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tsf_tournament_podiums: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tsf_best_position_points: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Timestamps
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")
    event_type: Mapped[Optional["EventType"]] = relationship("EventType", lazy="joined")
    largest_catch_species: Mapped[Optional["Fish"]] = relationship("Fish", lazy="joined")
    last_event: Mapped[Optional["Event"]] = relationship("Event", lazy="joined")

    def __repr__(self) -> str:
        event_type_name = self.event_type.name if self.event_type else "overall"
        return f"<UserEventTypeStats(id={self.id}, user_id={self.user_id}, type={event_type_name})>"


# Import for type hints (avoid circular imports)
from app.models.user import UserAccount
from app.models.event import Event, EventType
from app.models.fish import Fish
