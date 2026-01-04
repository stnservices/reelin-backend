"""
Trout Shore Fishing (TSF) Competition Models.

TSF competitions use a multi-day, group-based positional scoring system:
1. Participants are divided into groups/sectors
2. Competition runs over multiple days (configurable, usually 2)
3. Each day has multiple legs
4. Participants are ranked by position within their sector each leg
5. Lower position total = better ranking (golf-style scoring)

Key concepts:
- Days: Competition spans multiple days with daily rankings
- Sectors/Groups: Participants compete within their assigned sector
- Position Scoring: 1st=1pt, 2nd=2pt, 3rd=3pt... (lower is better)
- Final ranking based on sum of all position points across all days
"""

from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric,
    String, Text, UniqueConstraint, CheckConstraint, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# =============================================================================
# ENUMS
# =============================================================================

class TSFDayStatus(str, Enum):
    """Status of a TSF competition day."""
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TSFLegStatus(str, Enum):
    """Status of a TSF leg."""
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    SCORING = "scoring"        # Results being recorded
    COMPLETED = "completed"


# =============================================================================
# EVENT SETTINGS
# =============================================================================

class TSFEventSettings(Base):
    """
    TSF-specific event configuration.
    Defines number of days, sectors, legs per day, scoring rules.
    """
    __tablename__ = "tsf_event_settings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True
    )

    # Day Configuration (dynamic number of days)
    number_of_days: Mapped[int] = mapped_column(Integer, default=2, nullable=False)

    # Sector/Group Configuration
    number_of_sectors: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    participants_per_sector: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Auto-calculate if null

    # Leg Configuration
    legs_per_day: Mapped[int] = mapped_column(Integer, default=4, nullable=False)

    # Scoring Settings
    scoring_direction: Mapped[str] = mapped_column(String(10), default="lower", nullable=False)  # "lower" = lower is better
    ghost_position_penalty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # Extra points for ghost/DNF

    # Tiebreaker rules (JSON array of criteria)
    tiebreaker_rules: Mapped[list] = mapped_column(
        JSONB,
        default=["total_position_points", "first_places", "second_places", "best_single_leg"],
        nullable=False
    )

    # Rotation settings
    rotate_sectors_daily: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    seat_rotation_pattern: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Format: {"day_1_leg_1": [1,2,3,4], "day_1_leg_2": [4,1,2,3], ...}

    # Additional rules
    additional_rules: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="tsf_settings")

    def __repr__(self) -> str:
        return f"<TSFEventSettings(event_id={self.event_id}, days={self.number_of_days}, sectors={self.number_of_sectors})>"


# =============================================================================
# EVENT POINT CONFIG (Per-event customizable point values)
# =============================================================================

class TSFEventPointConfig(Base):
    """
    Per-event point value configuration for TSF match-based scoring.
    Allows organizers to customize point values instead of using defaults.

    Note: TSF can use either position-based scoring (1st=1pt, 2nd=2pt...)
    or match-based scoring (V/T/L). This config is used when match-based
    scoring is enabled.
    """
    __tablename__ = "tsf_event_point_configs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True
    )

    # Point values (defaults match standard rules)
    victory_points: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), default=Decimal("3.0"), nullable=False
    )
    tie_points: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), default=Decimal("1.5"), nullable=False
    )
    tie_zero_points: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), default=Decimal("1.0"), nullable=False
    )
    loss_points: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), default=Decimal("0.5"), nullable=False
    )
    loss_zero_points: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), default=Decimal("0.0"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="tsf_point_config")

    def __repr__(self) -> str:
        return f"<TSFEventPointConfig(event_id={self.event_id}, V={self.victory_points})>"


# =============================================================================
# DAYS
# =============================================================================

class TSFDay(Base):
    """
    Represents a competition day in TSF.
    Tracks daily schedule, status, and rankings.
    """
    __tablename__ = "tsf_days"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 2, 3...

    # Schedule
    scheduled_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Status
    status: Mapped[str] = mapped_column(
        String(20), default=TSFDayStatus.SCHEDULED.value, nullable=False
    )

    # Day notes/conditions
    weather_conditions: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="tsf_days")
    legs: Mapped[list["TSFLeg"]] = relationship(
        "TSFLeg", back_populates="day", cascade="all, delete-orphan"
    )
    day_standings: Mapped[list["TSFDayStanding"]] = relationship(
        "TSFDayStanding", back_populates="day", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("event_id", "day_number", name="uq_tsf_day_number"),
        CheckConstraint("day_number >= 1", name="ck_tsf_day_positive"),
    )

    def __repr__(self) -> str:
        return f"<TSFDay(event_id={self.event_id}, day={self.day_number}, status={self.status})>"


# =============================================================================
# LEGS
# =============================================================================

class TSFLeg(Base):
    """
    A single leg within a TSF day.
    Each leg has position results for all participants.
    """
    __tablename__ = "tsf_legs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_id: Mapped[int] = mapped_column(
        ForeignKey("tsf_days.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)
    leg_number: Mapped[int] = mapped_column(Integer, nullable=False)  # Within the day

    # Status
    status: Mapped[str] = mapped_column(
        String(20), default=TSFLegStatus.SCHEDULED.value, nullable=False
    )

    # Timing
    scheduled_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event")
    day: Mapped["TSFDay"] = relationship("TSFDay", back_populates="legs")
    positions: Mapped[list["TSFLegPosition"]] = relationship(
        "TSFLegPosition", back_populates="leg", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("event_id", "day_number", "leg_number", name="uq_tsf_leg"),
        CheckConstraint("day_number >= 1", name="ck_tsf_leg_day_positive"),
        CheckConstraint("leg_number >= 1", name="ck_tsf_leg_number_positive"),
    )

    def __repr__(self) -> str:
        return f"<TSFLeg(event_id={self.event_id}, day={self.day_number}, leg={self.leg_number})>"


# =============================================================================
# LINEUP (GROUP ASSIGNMENTS)
# =============================================================================

class TSFLineup(Base):
    """
    Group/sector assignment for TSF events.
    Assigns each participant to a sector for the competition.
    """
    __tablename__ = "tsf_lineups"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Participant
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    enrollment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("event_enrollments.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # Club membership at enrollment time (for club-based reporting)
    club_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Assignments
    draw_number: Mapped[int] = mapped_column(Integer, nullable=False)
    group_number: Mapped[int] = mapped_column(Integer, nullable=False)   # Sector/group (1, 2, 3, 4...)
    seat_index: Mapped[int] = mapped_column(Integer, nullable=False)     # Position within group

    # Ghost participant
    is_ghost: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="tsf_lineups")
    user: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[user_id], lazy="joined"
    )
    enrollment: Mapped[Optional["EventEnrollment"]] = relationship(
        "EventEnrollment", lazy="joined"
    )
    club: Mapped[Optional["Club"]] = relationship("Club", lazy="joined")
    created_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[created_by_id]
    )

    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_tsf_lineup_user"),
        UniqueConstraint("event_id", "draw_number", name="uq_tsf_lineup_draw"),
        CheckConstraint("group_number >= 1", name="ck_tsf_lineup_group_positive"),
        CheckConstraint("seat_index >= 1", name="ck_tsf_lineup_seat_positive"),
    )

    def __repr__(self) -> str:
        return f"<TSFLineup(event={self.event_id}, group={self.group_number}, draw={self.draw_number})>"


# =============================================================================
# SECTOR VALIDATORS
# =============================================================================

class TSFSectorValidator(Base):
    """
    Sector validator/arbiter assignment for TSF events.
    Each sector has a dedicated validator who enters results for all participants.
    Fast-paced competition - validators enter positions, no self-validation.
    """
    __tablename__ = "tsf_sector_validators"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Validator user
    validator_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Sector assignment
    sector_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Optional: backup validator
    backup_validator_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event")
    validator: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[validator_id], lazy="joined"
    )
    backup_validator: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[backup_validator_id]
    )

    __table_args__ = (
        UniqueConstraint("event_id", "sector_number", name="uq_tsf_sector_validator"),
        UniqueConstraint("event_id", "validator_id", name="uq_tsf_validator_user"),
        CheckConstraint("sector_number >= 1", name="ck_tsf_sector_positive"),
    )

    def __repr__(self) -> str:
        return f"<TSFSectorValidator(event={self.event_id}, sector={self.sector_number}, validator={self.validator_id})>"


# =============================================================================
# LEG POSITIONS
# =============================================================================

class TSFLegPosition(Base):
    """
    Position result for a participant in a specific leg.
    Records where each participant finished within their sector for this leg.
    """
    __tablename__ = "tsf_leg_positions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leg_id: Mapped[int] = mapped_column(
        ForeignKey("tsf_legs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Participant
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Position data
    group_number: Mapped[int] = mapped_column(Integer, nullable=False)
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)
    leg_number: Mapped[int] = mapped_column(Integer, nullable=False)
    seat_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # Result
    position_value: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 = 1st, 2 = 2nd, etc.

    # Optional catch data (for display, not primary scoring)
    fish_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_length: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Running totals (calculated for ranking display)
    best_checksum: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # Best possible sum so far
    worst_checksum: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Worst possible sum so far
    running_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # Actual running total

    # Ghost/DNF
    is_ghost: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_dnf: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # Did not finish

    # Validator tracking (sector validator enters all results)
    validated_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    validated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Edit tracking (for corrections by validators/organizers)
    edited_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    edited_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    previous_fish_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    previous_position_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event")
    leg: Mapped["TSFLeg"] = relationship("TSFLeg", back_populates="positions")
    user: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[user_id]
    )
    validated_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[validated_by_id]
    )

    __table_args__ = (
        UniqueConstraint("event_id", "leg_id", "user_id", name="uq_tsf_leg_position_user"),
        UniqueConstraint("event_id", "leg_id", "group_number", "position_value", name="uq_tsf_leg_position_rank"),
        CheckConstraint("position_value >= 1", name="ck_tsf_position_positive"),
    )

    def __repr__(self) -> str:
        return f"<TSFLegPosition(leg={self.leg_id}, user={self.user_id}, position={self.position_value})>"


# =============================================================================
# DAY STANDINGS
# =============================================================================

class TSFDayStanding(Base):
    """
    Daily standings for TSF event.
    Aggregates leg positions for each day.
    """
    __tablename__ = "tsf_day_standings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_id: Mapped[int] = mapped_column(
        ForeignKey("tsf_days.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Participant
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Day totals
    total_position_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # Lower is better
    legs_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_places: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    second_places: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    third_places: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    best_single_leg: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Best position achieved
    worst_single_leg: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Optional aggregated catch data
    total_fish_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_length: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Daily rank (within their sector)
    sector_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Overall daily rank (across all sectors)
    overall_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Leg-by-leg breakdown
    leg_positions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # Format: {"1": 2, "2": 1, "3": 3, "4": 2}  (leg_number: position)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event")
    day: Mapped["TSFDay"] = relationship("TSFDay", back_populates="day_standings")
    user: Mapped["UserAccount"] = relationship("UserAccount")

    __table_args__ = (
        UniqueConstraint("event_id", "day_id", "user_id", name="uq_tsf_day_standing"),
    )

    def __repr__(self) -> str:
        return f"<TSFDayStanding(day={self.day_number}, user={self.user_id}, points={self.total_position_points})>"


# =============================================================================
# FINAL STANDINGS
# =============================================================================

class TSFFinalStanding(Base):
    """
    Final overall standings for TSF event.
    Aggregates all days for final ranking.
    """
    __tablename__ = "tsf_final_standings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Participant
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enrollment_id: Mapped[int] = mapped_column(
        ForeignKey("event_enrollments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Overall totals (across all days)
    total_position_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    days_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    legs_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Placement counts
    total_first_places: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_second_places: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_third_places: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Best/worst performance
    best_single_leg: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    worst_single_leg: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    best_day_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    worst_day_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Catch statistics (optional)
    total_fish_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_length: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Final ranking
    final_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    # Day-by-day breakdown
    day_totals: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # Format: {"1": {"points": 8, "rank": 2}, "2": {"points": 6, "rank": 1}}

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="tsf_final_standings")
    user: Mapped["UserAccount"] = relationship("UserAccount")
    enrollment: Mapped["EventEnrollment"] = relationship("EventEnrollment")

    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_tsf_final_standing"),
    )

    def __repr__(self) -> str:
        return f"<TSFFinalStanding(event={self.event_id}, user={self.user_id}, rank={self.final_rank})>"


# =============================================================================
# IMPORTS FOR TYPE HINTS
# =============================================================================

from app.models.event import Event
from app.models.user import UserAccount
from app.models.enrollment import EventEnrollment
from app.models.club import Club
