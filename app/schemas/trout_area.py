"""Pydantic schemas for Trout Area (TA) competitions."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# =============================================================================
# Enums for API
# =============================================================================

class TAMatchOutcomeAPI(str, Enum):
    """Match outcome codes for API."""
    VICTORY = "V"
    TIE_WITH_FISH = "T"
    TIE_NO_FISH = "T0"
    LOSS_WITH_FISH = "L"
    LOSS_NO_FISH = "L0"


class TATournamentPhaseAPI(str, Enum):
    """Tournament phase for API."""
    QUALIFIER = "qualifier"
    REQUALIFICATION = "requalification"
    SEMIFINAL = "semifinal"
    FINAL_GRAND = "final_grand"
    FINAL_SMALL = "final_small"


class TAMatchStatusAPI(str, Enum):
    """Match status for API."""
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TAGameCardStatusAPI(str, Enum):
    """Game card status for API."""
    DRAFT = "draft"
    SUBMITTED = "submitted"
    VALIDATED = "validated"
    DISPUTED = "disputed"


class PairingAlgorithmAPI(str, Enum):
    """Pairing algorithm options for API."""
    ROUND_ROBIN_FULL = "round_robin_full"
    ROUND_ROBIN_HALF = "round_robin_half"
    ROUND_ROBIN_CUSTOM = "round_robin_custom"
    SIMPLE_PAIRS = "simple_pairs"


# =============================================================================
# Points Rules Schemas
# =============================================================================

class TAPointsRuleResponse(BaseModel):
    """Response schema for TA points rule."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    points: Decimal
    label: str
    description: Optional[str] = None
    is_active: bool


class TAPointsRuleCreate(BaseModel):
    """Create schema for TA points rule."""
    code: str = Field(..., min_length=1, max_length=10)
    points: Decimal = Field(..., ge=Decimal("0"))
    label: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = None


class TAPointsRuleUpdate(BaseModel):
    """Update schema for TA points rule."""
    points: Decimal = Field(..., ge=Decimal("0"), le=Decimal("99.99"))
    label: Optional[str] = Field(default=None, min_length=1, max_length=50)
    description: Optional[str] = None


class TAGlobalPointDefaultsResponse(BaseModel):
    """Response schema for global point defaults (structured like event config)."""
    victory_points: Decimal
    tie_points: Decimal
    tie_zero_points: Decimal
    loss_points: Decimal
    loss_zero_points: Decimal


class TAGlobalPointDefaultsUpdate(BaseModel):
    """Update schema for global point defaults."""
    victory_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))
    tie_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))
    tie_zero_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))
    loss_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))
    loss_zero_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))

    @model_validator(mode='after')
    def validate_point_order(self) -> 'TAGlobalPointDefaultsUpdate':
        """Ensure points are in logical order when all values provided."""
        # Only validate if all values are provided
        if all(v is not None for v in [
            self.victory_points, self.tie_points, self.tie_zero_points,
            self.loss_points, self.loss_zero_points
        ]):
            if self.victory_points < self.tie_points:
                raise ValueError("Victory points must be >= tie points")
            if self.tie_points < self.tie_zero_points:
                raise ValueError("Tie points must be >= tie_zero points")
            if self.tie_zero_points < self.loss_points:
                raise ValueError("Tie_zero points must be >= loss points")
            if self.loss_points < self.loss_zero_points:
                raise ValueError("Loss points must be >= loss_zero points")
        return self


# =============================================================================
# Event Point Config Schemas (Per-event customizable point values)
# =============================================================================

class TAEventPointConfigBase(BaseModel):
    """Base schema for per-event point configuration."""
    victory_points: Decimal = Field(default=Decimal("3.0"), ge=Decimal("0"), le=Decimal("99.99"))
    tie_points: Decimal = Field(default=Decimal("1.5"), ge=Decimal("0"), le=Decimal("99.99"))
    tie_zero_points: Decimal = Field(default=Decimal("1.0"), ge=Decimal("0"), le=Decimal("99.99"))
    loss_points: Decimal = Field(default=Decimal("0.5"), ge=Decimal("0"), le=Decimal("99.99"))
    loss_zero_points: Decimal = Field(default=Decimal("0.0"), ge=Decimal("0"), le=Decimal("99.99"))

    @model_validator(mode='after')
    def validate_point_order(self) -> 'TAEventPointConfigBase':
        """Ensure points are in logical order: victory >= tie >= tie_zero >= loss >= loss_zero."""
        if self.victory_points < self.tie_points:
            raise ValueError("Victory points must be >= tie points")
        if self.tie_points < self.tie_zero_points:
            raise ValueError("Tie points must be >= tie_zero points")
        if self.tie_zero_points < self.loss_points:
            raise ValueError("Tie_zero points must be >= loss points")
        if self.loss_points < self.loss_zero_points:
            raise ValueError("Loss points must be >= loss_zero points")
        return self


class TAEventPointConfigResponse(TAEventPointConfigBase):
    """Response schema for event point config."""
    model_config = ConfigDict(from_attributes=True)

    is_default: bool = False


class TAEventPointConfigUpdate(BaseModel):
    """Update schema for event point config (all fields optional)."""
    victory_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))
    tie_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))
    tie_zero_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))
    loss_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))
    loss_zero_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))

    @model_validator(mode='after')
    def validate_point_order(self) -> 'TAEventPointConfigUpdate':
        """Ensure points are in logical order when all values provided."""
        # Only validate if all values are provided
        if all(v is not None for v in [
            self.victory_points, self.tie_points, self.tie_zero_points,
            self.loss_points, self.loss_zero_points
        ]):
            if self.victory_points < self.tie_points:
                raise ValueError("Victory points must be >= tie points")
            if self.tie_points < self.tie_zero_points:
                raise ValueError("Tie points must be >= tie_zero points")
            if self.tie_zero_points < self.loss_points:
                raise ValueError("Tie_zero points must be >= loss points")
            if self.loss_points < self.loss_zero_points:
                raise ValueError("Loss points must be >= loss_zero points")
        return self


# =============================================================================
# Event Settings Schemas
# =============================================================================

class TAEventSettingsBase(BaseModel):
    """Base schema for TA event settings."""
    match_duration_minutes: int = Field(default=15, ge=5, le=60)
    legs_per_match: int = Field(default=1, ge=1, le=5)
    matches_per_leg: Optional[int] = None
    total_legs: Optional[int] = None
    pairing_algorithm: PairingAlgorithmAPI = PairingAlgorithmAPI.ROUND_ROBIN_FULL
    has_knockout_bracket: bool = Field(default=True, description="Whether event includes knockout bracket phase")
    qualification_top_n: int = Field(default=4, ge=2, description="Total semifinal slots")
    direct_to_semifinal: int = Field(default=2, ge=1, le=8, description="How many bypass requalification and go direct to semifinals")
    requalification_spots: int = Field(default=4, ge=0, description="How many compete in requalification (winners = spots/2)")
    enable_requalification: bool = Field(default=True)
    enable_team_scoring: bool = Field(default=False)
    team_size: Optional[int] = Field(default=None, ge=2, le=10)
    bracket_config: dict[str, Any] = Field(default_factory=dict)
    additional_rules: dict[str, Any] = Field(default_factory=dict)


class TAEventSettingsCreate(TAEventSettingsBase):
    """Create schema for TA event settings."""
    event_id: int


class TAEventSettingsUpdate(BaseModel):
    """Update schema for TA event settings."""
    match_duration_minutes: Optional[int] = Field(default=None, ge=5, le=60)
    legs_per_match: Optional[int] = Field(default=None, ge=1, le=5)
    matches_per_leg: Optional[int] = None
    total_legs: Optional[int] = None
    pairing_algorithm: Optional[PairingAlgorithmAPI] = None
    has_knockout_bracket: Optional[bool] = Field(default=None, description="Whether event includes knockout bracket phase")
    qualification_top_n: Optional[int] = Field(default=None, ge=2)
    direct_to_semifinal: Optional[int] = Field(default=None, ge=1, le=8)
    requalification_spots: Optional[int] = Field(default=None, ge=0)
    enable_requalification: Optional[bool] = None
    enable_team_scoring: Optional[bool] = None
    team_size: Optional[int] = Field(default=None, ge=2, le=10)
    bracket_config: Optional[dict[str, Any]] = None
    additional_rules: Optional[dict[str, Any]] = None


class TAEventSettingsResponse(BaseModel):
    """Response schema for TA event settings."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    match_duration_minutes: Optional[int] = None
    legs_per_match: int = 5  # Maps to number_of_legs
    matches_per_leg: Optional[int] = None
    total_legs: Optional[int] = None
    pairing_algorithm: PairingAlgorithmAPI = PairingAlgorithmAPI.ROUND_ROBIN_FULL
    has_knockout_bracket: bool = True  # Maps to has_knockout_stage
    qualification_top_n: int = 4  # Maps to knockout_qualifiers (total semifinal slots)
    direct_to_semifinal: int = 2  # How many bypass requalification
    requalification_spots: int = 4  # Maps to requalification_slots (positions compete)
    enable_requalification: bool = True  # Maps to has_requalification
    enable_team_scoring: bool = False  # Maps to is_team_event
    team_size: Optional[int] = None
    bracket_config: dict[str, Any] = {}
    additional_rules: dict[str, Any] = {}
    current_phase: TATournamentPhaseAPI = TATournamentPhaseAPI.QUALIFIER
    current_leg: int = 1
    draw_completed: bool = False
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def map_model_fields(cls, values: Any) -> Any:
        """Map model field names to API field names."""
        if hasattr(values, "__dict__"):
            # ORM model object
            data = {}
            for key in ["id", "event_id", "match_duration_minutes", "team_size",
                        "created_at", "updated_at", "additional_rules"]:
                data[key] = getattr(values, key, None)
            data["legs_per_match"] = getattr(values, "number_of_legs", 5)
            data["has_knockout_bracket"] = getattr(values, "has_knockout_stage", True)
            data["qualification_top_n"] = getattr(values, "knockout_qualifiers", 4)
            data["direct_to_semifinal"] = getattr(values, "direct_to_semifinal", 2)
            data["requalification_spots"] = getattr(values, "requalification_slots", 4)
            data["enable_requalification"] = getattr(values, "has_requalification", True)
            data["enable_team_scoring"] = getattr(values, "is_team_event", False)
            # Get from additional_rules
            add_rules = getattr(values, "additional_rules", {}) or {}
            data["current_phase"] = add_rules.get("current_phase", "qualifier")
            data["current_leg"] = add_rules.get("current_leg", add_rules.get("current_round", 1))
            data["draw_completed"] = add_rules.get("draw_completed", False)
            data["total_legs"] = add_rules.get("total_legs", add_rules.get("total_rounds"))
            data["matches_per_leg"] = add_rules.get("matches_per_leg", add_rules.get("matches_per_round"))
            data["bracket_config"] = add_rules.get("bracket_config", {})
            data["pairing_algorithm"] = add_rules.get("pairing_algorithm", "round_robin_full")
            return data
        return values


# =============================================================================
# Lineup Schemas
# =============================================================================

class TALineupBase(BaseModel):
    """Base schema for TA lineup."""
    draw_number: int = Field(..., ge=1)
    sector: int = Field(..., ge=1)
    initial_seat: int = Field(..., ge=1)
    is_ghost: bool = False


class TALineupCreate(TALineupBase):
    """Create schema for TA lineup (single entry)."""
    event_id: int
    user_id: Optional[int] = None
    enrollment_id: Optional[int] = None


class TALineupResponse(TALineupBase):
    """Response schema for TA lineup."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    user_id: Optional[int]
    enrollment_id: Optional[int]
    team_id: Optional[int]
    club_id: Optional[int] = None
    created_at: datetime

    # Nested user info
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None
    club_name: Optional[str] = None


class TALineupListResponse(BaseModel):
    """Response schema for listing lineups."""
    items: list[TALineupResponse]
    total: int
    has_ghost: bool
    sectors: int


# =============================================================================
# Generate Lineup Request
# =============================================================================

class TAGenerateLineupRequest(BaseModel):
    """Request schema for generating TA lineups."""
    algorithm: PairingAlgorithmAPI = PairingAlgorithmAPI.ROUND_ROBIN_FULL
    custom_legs: Optional[int] = Field(default=None, ge=1, le=50)
    shuffle_seed: Optional[int] = None  # For reproducible shuffling

    @field_validator("custom_legs")
    @classmethod
    def validate_custom_legs(cls, v, info):
        if info.data.get("algorithm") == PairingAlgorithmAPI.ROUND_ROBIN_CUSTOM and v is None:
            raise ValueError("custom_legs is required for ROUND_ROBIN_CUSTOM algorithm")
        return v


class TAGenerateLineupResponse(BaseModel):
    """Response schema for generated lineups."""
    message: str
    total_participants: int
    real_participants: int
    has_ghost: bool
    algorithm: str
    total_legs: int
    matches_per_leg: int
    total_matches: int
    estimated_duration: str
    lineups: list[TALineupResponse]
    schedule_preview: Optional[dict[str, Any]] = None


class TAGenerateBracketResponse(BaseModel):
    """Response schema for generated knockout bracket."""
    message: str
    matches_created: int
    has_requalification: bool


# =============================================================================
# Match Schemas
# =============================================================================

class TAMatchBase(BaseModel):
    """Base schema for TA match."""
    leg_number: int = Field(..., ge=1)
    match_number: int = Field(..., ge=1)


class TAMatchResultUpdate(BaseModel):
    """Update schema for editing TA match results."""
    competitor_a_catches: Optional[int] = Field(default=None, ge=0, description="Catches for competitor A")
    competitor_b_catches: Optional[int] = Field(default=None, ge=0, description="Catches for competitor B")
    status: Optional[str] = Field(default=None, description="Match status override")


class TAMatchResponse(TAMatchBase):
    """Response schema for TA match."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    phase: TATournamentPhaseAPI
    player_a_id: Optional[int]
    player_b_id: Optional[int]
    seat_a: int
    seat_b: int
    player_a_catches: Optional[int] = None
    player_b_catches: Optional[int] = None
    player_a_points: Optional[float] = None
    player_b_points: Optional[float] = None
    player_a_outcome: Optional[TAMatchOutcomeAPI] = None
    player_b_outcome: Optional[TAMatchOutcomeAPI] = None
    status: TAMatchStatusAPI
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    # Nested player info
    player_a_name: Optional[str] = None
    player_b_name: Optional[str] = None
    player_a_avatar: Optional[str] = None
    player_b_avatar: Optional[str] = None


class TAMatchDetailResponse(TAMatchResponse):
    """Detailed response schema for TA match with game cards."""
    game_cards: list["TAGameCardResponse"] = []


class TAMatchListResponse(BaseModel):
    """Response schema for listing matches."""
    items: list[TAMatchResponse]
    total: int
    by_leg: dict[int, list[TAMatchResponse]] = {}


# =============================================================================
# Game Card Schemas (Per-User, Per-Leg)
# =============================================================================

class TAGameCardBase(BaseModel):
    """Base schema for TA game card."""
    my_catches: Optional[int] = Field(default=None, ge=0)


class TAGameCardCreate(TAGameCardBase):
    """Create schema for TA game card."""
    event_id: int
    match_id: int
    leg_number: int = Field(..., ge=1)
    user_id: int
    my_seat: int = Field(..., ge=1)
    opponent_id: Optional[int] = None
    opponent_seat: Optional[int] = None
    is_ghost_opponent: bool = False


class TAGameCardUpdate(BaseModel):
    """Update schema for TA game card."""
    my_catches: Optional[int] = Field(default=None, ge=0)


class TAGameCardResponse(BaseModel):
    """Response schema for TA game card."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    match_id: int
    leg_number: int
    phase: Optional[TATournamentPhaseAPI] = None  # Phase from match (qualifier, requalification, semifinal, etc.)
    user_id: int
    my_catches: Optional[int] = None
    my_seat: int
    opponent_id: Optional[int] = None
    opponent_catches: Optional[int] = None
    opponent_seat: Optional[int] = None

    # Submission & Validation (both directions)
    is_submitted: bool
    is_validated: bool = False  # Was MY catches validated by opponent?
    validated_at: Optional[datetime] = None
    i_validated_opponent: bool = False  # Did I validate opponent's catches?
    i_validated_at: Optional[datetime] = None

    # Dispute
    is_disputed: bool = False
    dispute_reason: Optional[str] = None

    # Status
    status: TAGameCardStatusAPI
    is_ghost_opponent: bool = False
    submitted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    # Points earned (from event point config)
    my_points: Optional[float] = None

    # Nested user info
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None
    opponent_name: Optional[str] = None
    opponent_avatar: Optional[str] = None


class TAGameCardSubmitRequest(BaseModel):
    """Request schema for submitting game card (enter catches)."""
    my_catches: int = Field(..., ge=0, description="Number of catches")


class TAGameCardValidateRequest(BaseModel):
    """Request schema for opponent validation (self-validation)."""
    is_valid: bool = True
    dispute_reason: Optional[str] = None


class TAGameCardAdminUpdateRequest(BaseModel):
    """Request schema for admin/validator to update game card."""
    my_catches: Optional[int] = Field(default=None, ge=0)
    is_submitted: Optional[bool] = None
    is_validated: Optional[bool] = None
    i_validated_opponent: Optional[bool] = None


class TAMyGameCardsResponse(BaseModel):
    """Response schema for listing user's game cards."""
    items: list[TAGameCardResponse]
    total: int
    current_leg: Optional[int] = None
    event_id: int


# =============================================================================
# Knockout Bracket Schemas
# =============================================================================

class TAKnockoutMatchResponse(BaseModel):
    """Response schema for knockout match."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    bracket_id: int
    bracket_round: int
    bracket_position: int
    player_1_id: Optional[int]
    player_2_id: Optional[int]
    player_1_seed: Optional[int]
    player_2_seed: Optional[int]
    winner_id: Optional[int]
    loser_id: Optional[int]
    match_id: Optional[int]
    next_match_id: Optional[int]
    loser_next_match_id: Optional[int]
    is_bye: bool
    status: TAMatchStatusAPI

    # Nested player info
    player_1_name: Optional[str] = None
    player_2_name: Optional[str] = None


class TAKnockoutBracketResponse(BaseModel):
    """Response schema for knockout bracket."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    bracket_type: str
    total_rounds: int
    participants_count: int
    created_at: datetime

    matches: list[TAKnockoutMatchResponse] = []


class TABracketGenerateRequest(BaseModel):
    """Request schema for generating knockout bracket."""
    qualification_top_n: Optional[int] = None  # Override settings if provided
    include_requalification: bool = True


# =============================================================================
# Qualifier Standing Schemas
# =============================================================================

class TAQualifierStandingResponse(BaseModel):
    """Response schema for qualifier standing."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    user_id: int
    rank: Optional[int] = None  # None for DQ users (Story 12.6)
    total_points: Decimal
    total_catches: int
    total_length: float
    matches_played: int
    victories: int
    ties: int
    losses: int
    updated_at: datetime

    # Nested user info
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None

    # Disqualification status (Story 12.6)
    is_disqualified: bool = False


class TAQualifierStandingListResponse(BaseModel):
    """Response schema for listing qualifier standings."""
    items: list[TAQualifierStandingResponse]
    total: int
    phase: TATournamentPhaseAPI
    qualified_count: int
    requalification_count: int
    has_knockout_bracket: bool = True
    available_phases: list[str] = ["qualifier"]


# =============================================================================
# Ranking Movement Schemas
# =============================================================================

class TARankingMovementResponse(BaseModel):
    """Response schema for ranking movement."""
    user_id: int
    user_name: str
    previous_rank: int
    current_rank: int
    change: int  # positive = improved, negative = dropped
    is_new_leader: bool = False
    total_points: Decimal


class TARankingUpdateResponse(BaseModel):
    """Response schema for ranking update."""
    message: str
    movements: list[TARankingMovementResponse]
    current_leader: Optional[TARankingMovementResponse] = None


# =============================================================================
# Duration Estimate Schemas
# =============================================================================

class TADurationEstimateRequest(BaseModel):
    """Request schema for duration estimate."""
    num_participants: int = Field(..., ge=2)
    algorithm: PairingAlgorithmAPI = PairingAlgorithmAPI.ROUND_ROBIN_FULL
    match_duration_minutes: int = Field(default=15, ge=5, le=60)
    break_between_rounds_minutes: int = Field(default=5, ge=0, le=30)
    custom_rounds: Optional[int] = Field(default=None, ge=1)


class TADurationEstimateResponse(BaseModel):
    """Response schema for duration estimate."""
    num_participants: int
    effective_participants: int  # After adding ghost if needed
    has_ghost: bool
    algorithm: str
    num_rounds: int
    matches_per_round: int
    total_matches: int
    matches_per_participant: int
    match_duration_minutes: int
    break_between_rounds_minutes: int
    total_match_time_minutes: int
    total_break_time_minutes: int
    total_duration_minutes: int
    total_duration_formatted: str


# =============================================================================
# Algorithm Preview Schemas (Before Starting Event)
# =============================================================================

class TAAlgorithmOption(BaseModel):
    """Single algorithm option with preview stats."""
    algorithm: PairingAlgorithmAPI
    name: str
    description: str
    legs: int
    matches_per_leg: int
    total_matches: int
    estimated_duration_formatted: str
    is_recommended: bool = False
    warning: Optional[str] = None


class TAAlgorithmPreviewResponse(BaseModel):
    """Response for algorithm preview before generating lineups."""
    event_id: int
    enrolled_count: int
    effective_participants: int  # After adding ghost if odd
    has_ghost: bool
    options: list[TAAlgorithmOption]
    recommended_algorithm: PairingAlgorithmAPI


# =============================================================================
# Enhanced Ranking Schemas (Leg-by-Leg with Detailed Stats)
# =============================================================================

class TACompetitorDetailedStats(BaseModel):
    """Detailed competitor stats for ranking - matches old code structure."""
    user_id: int
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None
    rank: int
    points: Decimal
    captures: int
    victories: int
    ties_with_fish: int
    ties_without_fish: int
    losses_with_fish: int
    losses_without_fish: int
    matches_played: int


class TALegRankingResponse(BaseModel):
    """Response schema for leg-by-leg ranking (cumulative up to specified leg)."""
    event_id: int
    leg_number: int
    phase: TATournamentPhaseAPI
    is_cumulative: bool = True
    rankings: list[TACompetitorDetailedStats]
    total_participants: int


class TAMatchResultDetail(BaseModel):
    """Detailed match result showing A vs B."""
    match_id: int
    leg_number: int
    round_number: int
    match_number: int
    # Competitor A
    competitor_a_id: Optional[int]
    competitor_a_name: Optional[str] = None
    competitor_a_catches: int
    competitor_a_outcome: Optional[TAMatchOutcomeAPI]
    competitor_a_points: Decimal
    # Competitor B
    competitor_b_id: Optional[int]
    competitor_b_name: Optional[str] = None
    competitor_b_catches: int
    competitor_b_outcome: Optional[TAMatchOutcomeAPI]
    competitor_b_points: Decimal
    # Status
    status: TAMatchStatusAPI
    is_ghost_match: bool = False


class TALegMatchesResponse(BaseModel):
    """Response schema for all matches in a leg."""
    event_id: int
    leg_number: int
    phase: TATournamentPhaseAPI
    matches: list[TAMatchResultDetail]
    total_matches: int


class TABestPerformance(BaseModel):
    """Best performance player stats."""
    user_id: int
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None
    avg_catches_per_match: float


class TABestRound(BaseModel):
    """Best round statistics."""
    round_number: int
    total_catches: int


class TAEventStatisticsResponse(BaseModel):
    """Event-level statistics for TA competition."""
    event_id: int
    total_participants: int
    total_matches: int
    completed_matches: int
    total_catches: int
    # Calculated stats
    average_catches_per_match: float = 0.0
    catches_per_participant: float = 0.0
    catches_per_match_per_competitor: float = 0.0
    average_catches_per_minute: float = 0.0
    # Best performers
    top_scorer: Optional[TACompetitorDetailedStats] = None
    most_catches: Optional[TACompetitorDetailedStats] = None
    most_victories: Optional[TACompetitorDetailedStats] = None
    # NEW: Enhanced stats
    best_performance: Optional[TABestPerformance] = None
    best_round: Optional[TABestRound] = None
    # Phase info
    current_phase: TATournamentPhaseAPI
    current_leg: int
    total_legs: int


# =============================================================================
# Schedule Schemas (for mobile app)
# =============================================================================

class TALegResponse(BaseModel):
    """Response schema for a leg (mansă) in the schedule."""
    leg_number: int
    phase: TATournamentPhaseAPI
    matches: list[TAMatchResponse]
    matches_completed: int
    total_matches: int
    is_current: bool = False
    is_completed: bool = False


# Keep TARoundResponse as alias for backwards compatibility
TARoundResponse = TALegResponse


class TAScheduleResponse(BaseModel):
    """Response schema for TA event schedule."""
    legs: list[TALegResponse]
    current_leg: Optional[int] = None
    total_legs: int
    matches_completed: int
    total_matches: int
    # Backwards compatibility aliases
    rounds: list[TALegResponse] = []
    current_round: Optional[int] = None
    total_rounds: int = 0

    @model_validator(mode="after")
    def sync_aliases(self):
        """Keep backwards compatibility aliases in sync."""
        self.rounds = self.legs
        self.current_round = self.current_leg
        self.total_rounds = self.total_legs
        return self


# Forward references
TAMatchDetailResponse.model_rebuild()
