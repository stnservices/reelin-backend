"""Event-related models: Event, EventType, ScoringConfig, EventPrize, EventScoringRule."""

from datetime import datetime
from enum import Enum
from typing import Optional

from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, Table, Column, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# Association table for ScoringConfig <-> EventType (many-to-many)
scoring_config_event_types = Table(
    "scoring_config_event_types",
    Base.metadata,
    Column("scoring_config_id", Integer, ForeignKey("scoring_configs.id", ondelete="CASCADE"), primary_key=True),
    Column("event_type_id", Integer, ForeignKey("event_types.id", ondelete="CASCADE"), primary_key=True),
)


class EventStatus(str, Enum):
    """Event status enum."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ONGOING = "ongoing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class EventType(Base):
    """
    Event type model (Street Fishing, Trout Area, Trout Shore, etc.).
    Configurable from admin panel.
    """

    __tablename__ = "event_types"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)  # street_fishing, trout_area, etc.
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    icon_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    scoring_configs: Mapped[list["ScoringConfig"]] = relationship(
        "ScoringConfig",
        secondary=scoring_config_event_types,
        back_populates="event_types",
        lazy="dynamic",
    )
    events: Mapped[list["Event"]] = relationship("Event", back_populates="event_type", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<EventType(id={self.id}, code={self.code})>"


class ScoringConfig(Base):
    """
    Scoring configuration that can be assigned to multiple event types.

    Two scoring types for Street Fishing:
    - top_x_by_species: Top N catches per species (slot-based)
    - top_x_overall: Top N catches globally regardless of species

    The `code` field identifies which scoring logic to use.
    """

    __tablename__ = "scoring_configs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Default values for new events using this config
    default_top_x: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    default_catch_slots: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    # Additional rules stored as JSONB
    rules: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships (many-to-many with EventType)
    event_types: Mapped[list["EventType"]] = relationship(
        "EventType",
        secondary=scoring_config_event_types,
        back_populates="scoring_configs",
        lazy="selectin",
    )
    events: Mapped[list["Event"]] = relationship("Event", back_populates="scoring_config", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<ScoringConfig(id={self.id}, code={self.code})>"


class Event(Base):
    """Main event/competition model."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(250), unique=True, index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Event configuration
    event_type_id: Mapped[int] = mapped_column(
        ForeignKey("event_types.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    scoring_config_id: Mapped[int] = mapped_column(
        ForeignKey("scoring_configs.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Schedule
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    registration_deadline: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Location
    location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("fishing_spots.id", ondelete="SET NULL"), nullable=True
    )
    location_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # Fallback if no FishingSpot

    # Meeting point (where participants gather before the event)
    meeting_point_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    meeting_point_lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    meeting_point_address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Ownership and management
    created_by_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(20), default=EventStatus.DRAFT.value, nullable=False, index=True
    )

    # Capacity
    max_participants: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Scoring settings (for top_x_overall method)
    top_x_overall: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Top X catches globally
    has_bonus_points: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Event classification flags
    is_team_event: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_national_event: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_tournament_event: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Team settings (when is_team_event=True)
    min_team_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_team_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Rules and additional info
    rule_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("organizer_rules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Custom/additional rules
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Media upload constraints
    allow_gallery_upload: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allowed_media_type: Mapped[str] = mapped_column(String(20), default="both", nullable=False)  # "image", "video", "both"
    max_video_duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # seconds (3, 4, or 5)

    # Participation fee (informational - payment handled offline)
    participation_fee: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    participation_fee_currency_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("currencies.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    event_type: Mapped["EventType"] = relationship("EventType", back_populates="events", lazy="joined")
    scoring_config: Mapped["ScoringConfig"] = relationship(
        "ScoringConfig", back_populates="events", lazy="joined"
    )
    location: Mapped[Optional["FishingSpot"]] = relationship("FishingSpot", lazy="joined")
    created_by: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[created_by_id], lazy="joined"
    )
    deleted_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[deleted_by_id], lazy="joined"
    )
    rule: Mapped[Optional["OrganizerRule"]] = relationship("OrganizerRule", lazy="joined")
    participation_fee_currency: Mapped[Optional["Currency"]] = relationship("Currency", lazy="joined")
    prizes: Mapped[list["EventPrize"]] = relationship(
        "EventPrize", back_populates="event", lazy="dynamic", cascade="all, delete-orphan"
    )
    scoring_rules: Mapped[list["EventScoringRule"]] = relationship(
        "EventScoringRule", back_populates="event", lazy="dynamic", cascade="all, delete-orphan"
    )
    fish_scoring: Mapped[list["EventFishScoring"]] = relationship(
        "EventFishScoring", back_populates="event", lazy="selectin", cascade="all, delete-orphan"
    )
    species_bonus_points: Mapped[list["EventSpeciesBonusPoints"]] = relationship(
        "EventSpeciesBonusPoints", back_populates="event", lazy="dynamic", cascade="all, delete-orphan"
    )
    enrollments: Mapped[list["EventEnrollment"]] = relationship(
        "EventEnrollment", back_populates="event", lazy="dynamic", cascade="all, delete-orphan"
    )
    catches: Mapped[list["Catch"]] = relationship(
        "Catch", back_populates="event", lazy="dynamic", cascade="all, delete-orphan"
    )
    scoreboards: Mapped[list["EventScoreboard"]] = relationship(
        "EventScoreboard", back_populates="event", lazy="dynamic", cascade="all, delete-orphan"
    )
    validators: Mapped[list["EventValidator"]] = relationship(
        "EventValidator", back_populates="event", lazy="dynamic", cascade="all, delete-orphan"
    )
    teams: Mapped[list["Team"]] = relationship(
        "Team", back_populates="event", lazy="dynamic", cascade="all, delete-orphan"
    )
    event_sponsors: Mapped[list["EventSponsor"]] = relationship(
        "EventSponsor", back_populates="event", lazy="dynamic", cascade="all, delete-orphan"
    )
    contestations: Mapped[list["EventContestation"]] = relationship(
        "EventContestation", back_populates="event", lazy="dynamic", cascade="all, delete-orphan"
    )

    @property
    def is_draft(self) -> bool:
        return self.status == EventStatus.DRAFT.value

    @property
    def is_published(self) -> bool:
        return self.status == EventStatus.PUBLISHED.value

    @property
    def is_ongoing(self) -> bool:
        return self.status == EventStatus.ONGOING.value

    @property
    def is_completed(self) -> bool:
        return self.status == EventStatus.COMPLETED.value

    def __repr__(self) -> str:
        return f"<Event(id={self.id}, name={self.name}, status={self.status})>"


class EventPrize(Base):
    """Event prize model."""

    __tablename__ = "event_prizes"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    place: Mapped[int] = mapped_column(Integer, nullable=False)  # 1st, 2nd, 3rd, etc.
    title: Mapped[str] = mapped_column(String(100), nullable=False)  # "1st Place", "Best Catch", etc.
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    value: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # Free text prize value
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="prizes")

    def __repr__(self) -> str:
        return f"<EventPrize(id={self.id}, place={self.place})>"


class EventFishScoring(Base):
    """
    Per-event, per-fish scoring configuration.
    Defines how each fish species is scored within a specific event.
    """

    __tablename__ = "event_fish_scoring"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fish_id: Mapped[int] = mapped_column(
        ForeignKey("fish.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # How many catches of this species count toward the score
    accountable_catch_slots: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    # Minimum length (cm) for full points
    accountable_min_length: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Points awarded for catches below min_length (usually 0 or penalty)
    under_min_length_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # For Top_X scoring: how many of this species count
    top_x_catches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Ordering for display
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="fish_scoring")
    fish: Mapped["Fish"] = relationship("Fish", lazy="joined")

    def __repr__(self) -> str:
        return f"<EventFishScoring(id={self.id}, event_id={self.event_id}, fish_id={self.fish_id})>"


class EventSpeciesBonusPoints(Base):
    """
    Species diversity bonus points configuration.
    Awards bonus points when a participant catches multiple species.
    """

    __tablename__ = "event_species_bonus_points"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Number of distinct species required
    species_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # Bonus points awarded at this threshold
    bonus_points: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="species_bonus_points")

    def __repr__(self) -> str:
        return f"<EventSpeciesBonusPoints(id={self.id}, event_id={self.event_id}, species_count={self.species_count})>"


class EventScoringRule(Base):
    """
    Per-event, per-fish scoring rules for flexible scoring.
    Allows customization beyond the base ScoringConfig.
    """

    __tablename__ = "event_scoring_rules"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fish_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("fish.id", ondelete="CASCADE"), nullable=True, index=True
    )  # NULL means applies to all fish

    # Scoring parameters
    min_length: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_length: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    points_per_cm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bonus_points: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Flexible formula stored as JSONB
    # Example: {"formula": "length * 10 + bonus", "conditions": {"min_length": 20}}
    points_formula: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="scoring_rules")
    fish: Mapped[Optional["Fish"]] = relationship("Fish", lazy="joined")

    def __repr__(self) -> str:
        return f"<EventScoringRule(id={self.id}, event_id={self.event_id})>"


# Import for type hints (avoid circular imports)
from app.models.location import FishingSpot
from app.models.user import UserAccount
from app.models.enrollment import EventEnrollment
from app.models.catch import Catch, EventScoreboard
from app.models.fish import Fish
from app.models.event_validator import EventValidator
from app.models.team import Team
from app.models.currency import Currency
