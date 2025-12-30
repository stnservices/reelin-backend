"""
Trout Area (TA) Competition Models.

TA competitions use a head-to-head match-based scoring system with multiple phases:
1. Qualifier Stage - Round-robin style with legs and seat rotations
2. Knockout Stage - Requalification, Semifinals, Finals (Finala Mare & Finala Mica)

Key concepts:
- Competitors are paired based on draw numbers (seat assignments)
- Self-validation: competitors validate catches between themselves
- Game Cards: record of each match with catches, validation, outcome
- Ghost participants: when odd number of enrolled users
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, Numeric,
    String, Text, UniqueConstraint, CheckConstraint, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# =============================================================================
# ENUMS
# =============================================================================

class TAMatchOutcome(str, Enum):
    """Match outcome codes for TA scoring."""
    VICTORY = "V"              # Won: 3.0 points
    TIE_WITH_FISH = "T"        # Tie, both caught fish: 1.5 points
    TIE_NO_FISH = "T0"         # Tie, neither caught: 1.0 points
    LOSS_WITH_FISH = "L"       # Lost but caught fish: 0.5 points
    LOSS_NO_FISH = "L0"        # Lost, no fish: 0.0 points


class TATournamentPhase(str, Enum):
    """Tournament phases in TA competition."""
    QUALIFIER = "qualifier"              # Round-robin qualifier stage
    REQUALIFICATION = "requalification"  # Second chance for lower ranked
    SEMIFINAL = "semifinal"              # Top 4 competitors
    FINAL_GRAND = "final_grand"          # Finala Mare: 1st & 2nd place
    FINAL_SMALL = "final_small"          # Finala Mica: 3rd & 4th place


class TAMatchStatus(str, Enum):
    """Status of a TA match."""
    SCHEDULED = "scheduled"       # Match scheduled, not started
    IN_PROGRESS = "in_progress"   # Match ongoing
    PENDING_VALIDATION = "pending_validation"  # Waiting for both to validate
    VALIDATED = "validated"       # Both competitors validated
    DISPUTED = "disputed"         # Validation dispute
    COMPLETED = "completed"       # Match finalized


class TAGameCardStatus(str, Enum):
    """Status of a game card."""
    DRAFT = "draft"
    SUBMITTED = "submitted"
    VALIDATED = "validated"
    DISPUTED = "disputed"


# =============================================================================
# POINT RULES
# =============================================================================

class TAPointsRule(Base):
    """
    Point values for TA match outcomes.
    Default values: V=3.0, T=1.5, T0=1.0, L=0.5, L0=0.0
    """
    __tablename__ = "ta_points_rules"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(5), unique=True, nullable=False)  # V, T, T0, L, L0
    points: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<TAPointsRule(code={self.code}, points={self.points})>"


class TAEventPointConfig(Base):
    """
    Per-event point value configuration for TA scoring.
    Allows organizers to customize point values instead of using defaults.
    """
    __tablename__ = "ta_event_point_configs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True
    )

    # Point values (defaults match standard TA rules)
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
    event: Mapped["Event"] = relationship("Event", back_populates="ta_point_config")

    def __repr__(self) -> str:
        return f"<TAEventPointConfig(event_id={self.event_id}, V={self.victory_points})>"


# =============================================================================
# EVENT SETTINGS
# =============================================================================

class TAEventSettings(Base):
    """
    TA-specific event configuration.
    Defines number of legs, knockout structure, team settings, etc.
    """
    __tablename__ = "ta_event_settings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True
    )

    # Qualifier Stage Settings
    number_of_legs: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    max_rounds_per_leg: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Knockout Stage Settings
    has_knockout_stage: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    knockout_qualifiers: Mapped[int] = mapped_column(Integer, default=6, nullable=False)  # Top N go to knockout
    has_requalification: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requalification_slots: Mapped[int] = mapped_column(Integer, default=4, nullable=False)  # How many compete in requalification

    # Direct placement for lower ranked (LOCUL 7, 8, 9, 10...)
    direct_placement_from: Mapped[int] = mapped_column(Integer, default=7, nullable=False)

    # Team Settings (optional team TA)
    is_team_event: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    team_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    team_scoring_method: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # sum, average, best_n

    # Validation Settings
    require_both_validation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_validate_ghost: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    dispute_resolution_timeout_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)

    # Match Settings
    match_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    break_between_legs_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Additional rules stored as JSON
    additional_rules: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="ta_settings")

    def __repr__(self) -> str:
        return f"<TAEventSettings(event_id={self.event_id}, legs={self.number_of_legs})>"


# =============================================================================
# LINEUP (DRAW ASSIGNMENTS)
# =============================================================================

class TALineup(Base):
    """
    Draw/seat assignment for TA events.
    Each participant gets a draw_number for each leg, determining their seat rotation.

    Seat pairing: (1,2), (3,4), (5,6), (7,8)...
    Seats rotate between legs according to a pattern (Circle Method).

    For N participants:
    - Odd sectors (1,3,5...): move -2 each leg (wrap if < 1)
    - Even sectors (2,4,6...): move +2 each leg (wrap if > N)
    - Special leg (middle): even sectors +4 instead of +2
    """
    __tablename__ = "ta_lineups"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leg_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Participant
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    enrollment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("event_enrollments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    team_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Position assignments
    draw_number: Mapped[int] = mapped_column(Integer, nullable=False)  # Original draw (1, 2, 3...)
    sector: Mapped[int] = mapped_column(Integer, nullable=False)       # Sector/zone for this leg
    seat_number: Mapped[int] = mapped_column(Integer, nullable=False)  # Seat position (1-based)

    # Ghost participant (for odd numbers)
    is_ghost: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Metadata
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def initial_seat(self) -> int:
        """Alias for seat_number for API compatibility."""
        return self.seat_number

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="ta_lineups")
    user: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[user_id], lazy="joined"
    )
    enrollment: Mapped[Optional["EventEnrollment"]] = relationship(
        "EventEnrollment", lazy="joined"
    )
    created_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[created_by_id]
    )

    __table_args__ = (
        UniqueConstraint("event_id", "leg_number", "draw_number", name="uq_ta_lineup_draw"),
        UniqueConstraint("event_id", "leg_number", "seat_number", name="uq_ta_lineup_seat"),
        CheckConstraint("leg_number >= 1", name="ck_ta_lineup_leg_positive"),
        CheckConstraint("draw_number >= 1", name="ck_ta_lineup_draw_positive"),
        CheckConstraint("sector >= 1", name="ck_ta_lineup_sector_positive"),
    )

    def __repr__(self) -> str:
        return f"<TALineup(event={self.event_id}, leg={self.leg_number}, draw={self.draw_number})>"


# =============================================================================
# GAME CARD (Per-User, Per-Leg)
# =============================================================================

class TAGameCard(Base):
    """
    Game Card for TA matches - ONE card per user per leg.

    Each competitor has their own game card where they:
    1. Enter their catches (my_catches)
    2. See opponent's catches (opponent_catches, from opponent's card)
    3. Validate opponent's entry (self-validation)

    Self-validation flow:
    1. User A enters catches → is_submitted = True
    2. User B enters catches → is_submitted = True
    3. Both see each other's catches
    4. Both validate → is_validated = True for both
    5. Match is complete when both cards are validated
    """
    __tablename__ = "ta_game_cards"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    match_id: Mapped[int] = mapped_column(
        ForeignKey("ta_matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leg_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Card owner
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # User's entry (what this user caught)
    my_catches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    my_seat: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Opponent info (for display - populated from opponent's card)
    opponent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    opponent_catches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    opponent_seat: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Submission & Validation status
    is_submitted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Was MY catches validated by opponent?
    is_validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validated_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    validated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Did I validate opponent's catches?
    i_validated_opponent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    i_validated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Dispute
    is_disputed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dispute_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dispute_resolved_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    dispute_resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Card status
    status: Mapped[str] = mapped_column(
        String(20), default=TAGameCardStatus.DRAFT.value, nullable=False
    )

    # For ghost matches (opponent is ghost)
    is_ghost_opponent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event")
    match: Mapped["TAMatch"] = relationship("TAMatch", back_populates="game_cards")
    user: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[user_id], lazy="joined"
    )
    opponent: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[opponent_id]
    )
    validated_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[validated_by_id]
    )
    dispute_resolved_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[dispute_resolved_by_id]
    )

    __table_args__ = (
        # Each user has one card per leg per event
        UniqueConstraint("event_id", "leg_number", "user_id", name="uq_ta_game_card_user_leg"),
    )

    def __repr__(self) -> str:
        return f"<TAGameCard(event={self.event_id}, leg={self.leg_number}, user={self.user_id})>"


# =============================================================================
# QUALIFIER MATCH
# =============================================================================

class TAMatch(Base):
    """
    Head-to-head match in TA qualifier stage.

    Matches are created based on seat pairings: (1,2), (3,4), (5,6)...
    Each leg has a set of matches.
    """
    __tablename__ = "ta_matches"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Match position
    phase: Mapped[str] = mapped_column(
        String(20), default=TATournamentPhase.QUALIFIER.value, nullable=False
    )
    leg_number: Mapped[int] = mapped_column(Integer, nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    match_number: Mapped[int] = mapped_column(Integer, nullable=False)  # Within the leg

    # Seat assignments
    seat_a: Mapped[int] = mapped_column(Integer, nullable=False)
    seat_b: Mapped[int] = mapped_column(Integer, nullable=False)

    # Competitor A
    competitor_a_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    competitor_a_enrollment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("event_enrollments.id", ondelete="SET NULL"), nullable=True
    )
    competitor_a_draw_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    competitor_a_catches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    competitor_a_points: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)
    competitor_a_outcome_code: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    is_valid_a: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Competitor B
    competitor_b_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    competitor_b_enrollment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("event_enrollments.id", ondelete="SET NULL"), nullable=True
    )
    competitor_b_draw_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    competitor_b_catches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    competitor_b_points: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)
    competitor_b_outcome_code: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    is_valid_b: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Ghost match (when odd participants)
    is_ghost_match: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ghost_side: Mapped[Optional[str]] = mapped_column(String(1), nullable=True)  # 'A' or 'B'

    # Status
    status: Mapped[str] = mapped_column(
        String(20), default=TAMatchStatus.SCHEDULED.value, nullable=False, index=True
    )

    # Edit tracking (for corrections by organizers)
    edited_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    edited_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    previous_a_catches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    previous_b_catches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Timestamps
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="ta_matches")
    competitor_a: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[competitor_a_id], lazy="joined"
    )
    competitor_b: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[competitor_b_id], lazy="joined"
    )
    competitor_a_enrollment: Mapped[Optional["EventEnrollment"]] = relationship(
        "EventEnrollment", foreign_keys=[competitor_a_enrollment_id]
    )
    competitor_b_enrollment: Mapped[Optional["EventEnrollment"]] = relationship(
        "EventEnrollment", foreign_keys=[competitor_b_enrollment_id]
    )
    game_cards: Mapped[list["TAGameCard"]] = relationship(
        "TAGameCard", back_populates="match", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("event_id", "phase", "leg_number", "round_number", "match_number",
                        name="uq_ta_match_position"),
        CheckConstraint("leg_number >= 1", name="ck_ta_match_leg_positive"),
        CheckConstraint("round_number >= 1", name="ck_ta_match_round_positive"),
        CheckConstraint("seat_a >= 1", name="ck_ta_match_seat_a_positive"),
        CheckConstraint("seat_b >= 1", name="ck_ta_match_seat_b_positive"),
    )

    def __repr__(self) -> str:
        return f"<TAMatch(event={self.event_id}, leg={self.leg_number}, seats={self.seat_a}-{self.seat_b})>"

    def calculate_outcome(
        self,
        point_config: Optional["TAEventPointConfig"] = None,
    ) -> None:
        """
        Calculate match outcome based on catches.

        Args:
            point_config: Optional custom point configuration.
                         If not provided, uses default point values.
        """
        if self.competitor_a_catches is None or self.competitor_b_catches is None:
            return

        # Get point values from config or use defaults
        if point_config:
            victory_pts = point_config.victory_points
            tie_pts = point_config.tie_points
            tie_zero_pts = point_config.tie_zero_points
            loss_pts = point_config.loss_points
            loss_zero_pts = point_config.loss_zero_points
        else:
            # Default point values
            victory_pts = Decimal("3.0")
            tie_pts = Decimal("1.5")
            tie_zero_pts = Decimal("1.0")
            loss_pts = Decimal("0.5")
            loss_zero_pts = Decimal("0.0")

        a_catches = self.competitor_a_catches
        b_catches = self.competitor_b_catches

        if a_catches > b_catches:
            self.competitor_a_outcome_code = TAMatchOutcome.VICTORY.value
            self.competitor_a_points = victory_pts
            if b_catches > 0:
                self.competitor_b_outcome_code = TAMatchOutcome.LOSS_WITH_FISH.value
                self.competitor_b_points = loss_pts
            else:
                self.competitor_b_outcome_code = TAMatchOutcome.LOSS_NO_FISH.value
                self.competitor_b_points = loss_zero_pts
        elif b_catches > a_catches:
            self.competitor_b_outcome_code = TAMatchOutcome.VICTORY.value
            self.competitor_b_points = victory_pts
            if a_catches > 0:
                self.competitor_a_outcome_code = TAMatchOutcome.LOSS_WITH_FISH.value
                self.competitor_a_points = loss_pts
            else:
                self.competitor_a_outcome_code = TAMatchOutcome.LOSS_NO_FISH.value
                self.competitor_a_points = loss_zero_pts
        else:  # Tie
            if a_catches > 0:
                self.competitor_a_outcome_code = TAMatchOutcome.TIE_WITH_FISH.value
                self.competitor_a_points = tie_pts
                self.competitor_b_outcome_code = TAMatchOutcome.TIE_WITH_FISH.value
                self.competitor_b_points = tie_pts
            else:
                self.competitor_a_outcome_code = TAMatchOutcome.TIE_NO_FISH.value
                self.competitor_a_points = tie_zero_pts
                self.competitor_b_outcome_code = TAMatchOutcome.TIE_NO_FISH.value
                self.competitor_b_points = tie_zero_pts


# =============================================================================
# KNOCKOUT BRACKET
# =============================================================================

class TAKnockoutBracket(Base):
    """
    Knockout stage bracket structure for TA events.

    Defines the bracket layout for:
    - Requalification (RECALIFICĂRI)
    - Semifinals (SEMIFINALA)
    - Finals (FINALA MARE, FINALA MICA)

    Based on qualifier rankings, competitors are seeded into the bracket.
    """
    __tablename__ = "ta_knockout_brackets"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True
    )

    # Bracket configuration
    total_qualifiers: Mapped[int] = mapped_column(Integer, nullable=False)  # How many advance from qualifier

    # Seeding from qualifier stage
    seeds: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # Format: {"1": user_id, "2": user_id, "3": user_id, ...}

    # Direct placements (for those who don't make knockout)
    direct_placements: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # Format: {"7": user_id, "8": user_id, ...}

    # Bracket status
    is_generated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Final results (after bracket completion)
    final_standings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # Format: {"1": user_id, "2": user_id, "3": user_id, "4": user_id, ...}

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="ta_knockout_bracket")
    knockout_matches: Mapped[list["TAKnockoutMatch"]] = relationship(
        "TAKnockoutMatch", back_populates="bracket", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<TAKnockoutBracket(event_id={self.event_id}, qualifiers={self.total_qualifiers})>"


class TAKnockoutMatch(Base):
    """
    Match in the knockout stage (requalification, semifinals, finals).

    Different from qualifier matches - these determine final placements.
    """
    __tablename__ = "ta_knockout_matches"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    bracket_id: Mapped[int] = mapped_column(
        ForeignKey("ta_knockout_brackets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Match position in bracket
    phase: Mapped[str] = mapped_column(String(20), nullable=False)  # requalification, semifinal, final_grand, final_small
    match_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # What happens to winner/loser
    winner_advances_to_phase: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    winner_advances_to_match: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    loser_advances_to_phase: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    loser_advances_to_match: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    winner_placement: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Final placement if this is final
    loser_placement: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # Final placement if this is final

    # Competitors
    competitor_a_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    competitor_a_seed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    competitor_a_catches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    competitor_a_is_winner: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    competitor_b_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    competitor_b_seed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    competitor_b_catches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    competitor_b_is_winner: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Status
    status: Mapped[str] = mapped_column(
        String(20), default=TAMatchStatus.SCHEDULED.value, nullable=False
    )

    # Timestamps
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    bracket: Mapped["TAKnockoutBracket"] = relationship("TAKnockoutBracket", back_populates="knockout_matches")
    event: Mapped["Event"] = relationship("Event")
    competitor_a: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[competitor_a_id]
    )
    competitor_b: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[competitor_b_id]
    )

    __table_args__ = (
        UniqueConstraint("bracket_id", "phase", "match_number", name="uq_ta_knockout_match"),
    )

    def __repr__(self) -> str:
        return f"<TAKnockoutMatch(bracket={self.bracket_id}, phase={self.phase}, match={self.match_number})>"


# =============================================================================
# QUALIFIER STANDINGS
# =============================================================================

class TAQualifierStanding(Base):
    """
    Running standings during TA qualifier stage.
    Aggregates results across all legs for each participant.
    """
    __tablename__ = "ta_qualifier_standings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enrollment_id: Mapped[int] = mapped_column(
        ForeignKey("event_enrollments.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Aggregated stats
    total_points: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"), nullable=False)
    total_matches: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_victories: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_ties: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_losses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_fish_caught: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Ranking
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    qualifies_for_knockout: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Breakdown by leg (JSON for flexibility)
    leg_results: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # Format: {"1": {"points": 6.0, "victories": 2, "fish": 15}, "2": {...}}

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event")
    user: Mapped["UserAccount"] = relationship("UserAccount")
    enrollment: Mapped["EventEnrollment"] = relationship("EventEnrollment")

    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_ta_standing_user"),
    )

    def __repr__(self) -> str:
        return f"<TAQualifierStanding(event={self.event_id}, user={self.user_id}, rank={self.rank})>"


# =============================================================================
# IMPORTS FOR TYPE HINTS
# =============================================================================

from app.models.event import Event
from app.models.user import UserAccount
from app.models.enrollment import EventEnrollment
