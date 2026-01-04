"""Pydantic schemas for Trout Shore Fishing (TSF) competitions."""

from datetime import datetime, date
from decimal import Decimal
from typing import Any, Optional
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


# =============================================================================
# Enums for API
# =============================================================================

class TSFDayStatusAPI(str, Enum):
    """Day status for API."""
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TSFLegStatusAPI(str, Enum):
    """Leg status for API."""
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# =============================================================================
# Event Point Config Schemas (Per-event customizable point values)
# =============================================================================

class TSFEventPointConfigBase(BaseModel):
    """Base schema for per-event point configuration."""
    victory_points: Decimal = Field(default=Decimal("3.0"), ge=Decimal("0"), le=Decimal("99.99"))
    tie_points: Decimal = Field(default=Decimal("1.5"), ge=Decimal("0"), le=Decimal("99.99"))
    tie_zero_points: Decimal = Field(default=Decimal("1.0"), ge=Decimal("0"), le=Decimal("99.99"))
    loss_points: Decimal = Field(default=Decimal("0.5"), ge=Decimal("0"), le=Decimal("99.99"))
    loss_zero_points: Decimal = Field(default=Decimal("0.0"), ge=Decimal("0"), le=Decimal("99.99"))

    @model_validator(mode='after')
    def validate_point_order(self) -> 'TSFEventPointConfigBase':
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


class TSFEventPointConfigResponse(TSFEventPointConfigBase):
    """Response schema for event point config."""
    model_config = ConfigDict(from_attributes=True)

    is_default: bool = False


class TSFEventPointConfigUpdate(BaseModel):
    """Update schema for event point config (all fields optional)."""
    victory_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))
    tie_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))
    tie_zero_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))
    loss_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))
    loss_zero_points: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("99.99"))

    @model_validator(mode='after')
    def validate_point_order(self) -> 'TSFEventPointConfigUpdate':
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

class TSFEventSettingsBase(BaseModel):
    """Base schema for TSF event settings."""
    number_of_days: int = Field(default=2, ge=1, le=10)
    number_of_sectors: int = Field(default=4, ge=2, le=20)
    participants_per_sector: Optional[int] = Field(default=None, ge=1)
    legs_per_day: int = Field(default=4, ge=1, le=10)
    scoring_direction: str = Field(default="lower")  # "lower" = lower points is better
    ghost_position_penalty: int = Field(default=0, ge=0)
    rotate_sectors_daily: bool = Field(default=True)
    seat_rotation_pattern: Optional[dict[str, Any]] = None
    tiebreaker_rules: list[str] = Field(default_factory=lambda: [
        "total_position_points",
        "first_places",
        "total_fish_count",
    ])
    additional_rules: dict[str, Any] = Field(default_factory=dict)


class TSFEventSettingsCreate(TSFEventSettingsBase):
    """Create schema for TSF event settings."""
    event_id: int


class TSFEventSettingsUpdate(BaseModel):
    """Update schema for TSF event settings."""
    number_of_days: Optional[int] = Field(default=None, ge=1, le=10)
    number_of_sectors: Optional[int] = Field(default=None, ge=2, le=20)
    participants_per_sector: Optional[int] = Field(default=None, ge=1)
    legs_per_day: Optional[int] = Field(default=None, ge=1, le=10)
    scoring_direction: Optional[str] = None
    ghost_position_penalty: Optional[int] = Field(default=None, ge=0)
    rotate_sectors_daily: Optional[bool] = None
    seat_rotation_pattern: Optional[dict[str, Any]] = None
    tiebreaker_rules: Optional[list[str]] = None
    additional_rules: Optional[dict[str, Any]] = None


class TSFEventSettingsResponse(TSFEventSettingsBase):
    """Response schema for TSF event settings."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    created_at: datetime
    updated_at: datetime


# =============================================================================
# Day Schemas
# =============================================================================

class TSFDayBase(BaseModel):
    """Base schema for TSF day."""
    day_number: int = Field(..., ge=1)
    scheduled_date: Optional[date] = None
    weather_conditions: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = None


class TSFDayCreate(TSFDayBase):
    """Create schema for TSF day."""
    event_id: int


class TSFDayUpdate(BaseModel):
    """Update schema for TSF day."""
    scheduled_date: Optional[date] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    status: Optional[TSFDayStatusAPI] = None
    weather_conditions: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = None


class TSFDayResponse(TSFDayBase):
    """Response schema for TSF day."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    status: TSFDayStatusAPI
    created_at: datetime
    updated_at: datetime

    # Nested leg count
    legs_count: int = 0
    completed_legs: int = 0


class TSFDayListResponse(BaseModel):
    """Response schema for listing days."""
    items: list[TSFDayResponse]
    total: int
    current_day: Optional[int] = None


# =============================================================================
# Leg Schemas
# =============================================================================

class TSFLegBase(BaseModel):
    """Base schema for TSF leg."""
    leg_number: int = Field(..., ge=1)


class TSFLegCreate(TSFLegBase):
    """Create schema for TSF leg."""
    event_id: int
    day_id: int
    day_number: int = Field(..., ge=1)
    scheduled_start: Optional[datetime] = None


class TSFLegUpdate(BaseModel):
    """Update schema for TSF leg."""
    scheduled_start: Optional[datetime] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    status: Optional[TSFLegStatusAPI] = None


class TSFLegResponse(TSFLegBase):
    """Response schema for TSF leg."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    day_id: int
    day_number: int
    scheduled_start: Optional[datetime]
    actual_start: Optional[datetime]
    actual_end: Optional[datetime]
    status: TSFLegStatusAPI
    created_at: datetime
    updated_at: datetime


class TSFLegListResponse(BaseModel):
    """Response schema for listing legs."""
    items: list[TSFLegResponse]
    total: int
    day_number: int


# =============================================================================
# Lineup Schemas
# =============================================================================

class TSFLineupBase(BaseModel):
    """Base schema for TSF lineup."""
    draw_number: int = Field(..., ge=1)
    group_number: int = Field(..., ge=1)
    seat_index: int = Field(..., ge=1)
    is_ghost: bool = False


class TSFLineupCreate(TSFLineupBase):
    """Create schema for TSF lineup."""
    event_id: int
    user_id: Optional[int] = None
    enrollment_id: Optional[int] = None


class TSFLineupResponse(TSFLineupBase):
    """Response schema for TSF lineup."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    user_id: Optional[int]
    enrollment_id: Optional[int]
    club_id: Optional[int] = None
    club_name: Optional[str] = None
    created_at: datetime

    # Nested user info
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None


class TSFLineupListResponse(BaseModel):
    """Response schema for listing lineups."""
    items: list[TSFLineupResponse]
    total: int
    has_ghost: bool
    groups: int
    participants_per_group: int


# =============================================================================
# Leg Position Schemas
# =============================================================================

class TSFLegPositionBase(BaseModel):
    """Base schema for TSF leg position."""
    position_value: int = Field(..., ge=1)
    fish_count: Optional[int] = Field(default=None, ge=0)
    total_length: Optional[float] = Field(default=None, ge=0)


class TSFLegPositionCreate(TSFLegPositionBase):
    """Create schema for TSF leg position."""
    event_id: int
    leg_id: int
    user_id: Optional[int] = None
    group_number: int = Field(..., ge=1)
    day_number: int = Field(..., ge=1)
    leg_number: int = Field(..., ge=1)
    seat_index: int = Field(..., ge=1)
    is_ghost: bool = False
    is_dnf: bool = False


class TSFLegPositionUpdate(BaseModel):
    """Update schema for TSF leg position."""
    position_value: Optional[int] = Field(default=None, ge=1)
    fish_count: Optional[int] = Field(default=None, ge=0)
    total_length: Optional[float] = Field(default=None, ge=0)
    is_dnf: Optional[bool] = None


class TSFLegPositionResponse(TSFLegPositionBase):
    """Response schema for TSF leg position."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    leg_id: int
    user_id: Optional[int]
    group_number: int
    day_number: int
    leg_number: int
    seat_index: int
    best_checksum: Optional[int]
    worst_checksum: Optional[int]
    running_total: Optional[int]
    is_ghost: bool
    is_dnf: bool
    created_at: datetime
    updated_at: datetime

    # Nested user info
    user_name: Optional[str] = None


class TSFLegPositionListResponse(BaseModel):
    """Response schema for listing leg positions."""
    items: list[TSFLegPositionResponse]
    total: int
    leg_id: int
    day_number: int
    leg_number: int


class TSFSubmitPositionsRequest(BaseModel):
    """Request schema for submitting multiple positions for a leg."""
    positions: list[TSFLegPositionCreate]


# =============================================================================
# Day Standing Schemas
# =============================================================================

class TSFDayStandingResponse(BaseModel):
    """Response schema for TSF day standing."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    day_id: int
    day_number: int
    user_id: int
    group_number: int
    total_position_points: int
    legs_completed: int
    first_places: int
    second_places: int
    third_places: int
    best_single_leg: Optional[int]
    worst_single_leg: Optional[int]
    total_fish_count: int
    total_length: float
    sector_rank: Optional[int]
    overall_rank: Optional[int]
    leg_positions: dict[str, Any]
    updated_at: datetime

    # Nested user info
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None


class TSFDayStandingListResponse(BaseModel):
    """Response schema for listing day standings."""
    items: list[TSFDayStandingResponse]
    total: int
    day_number: int
    groups: dict[int, list[TSFDayStandingResponse]] = {}


# =============================================================================
# Final Standing Schemas
# =============================================================================

class TSFFinalStandingResponse(BaseModel):
    """Response schema for TSF final standing."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    user_id: int
    enrollment_id: int
    group_number: int
    total_position_points: int
    days_completed: int
    legs_completed: int
    total_first_places: int
    total_second_places: int
    total_third_places: int
    best_single_leg: Optional[int]
    worst_single_leg: Optional[int]
    best_day_total: Optional[int]
    worst_day_total: Optional[int]
    total_fish_count: int
    total_length: float
    final_rank: Optional[int]
    day_totals: dict[str, Any]
    updated_at: datetime

    # Nested user info
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None


class TSFFinalStandingListResponse(BaseModel):
    """Response schema for listing final standings."""
    items: list[TSFFinalStandingResponse]
    total: int
    completed_days: int
    total_days: int


# =============================================================================
# Ranking Movement Schemas
# =============================================================================

class TSFRankingMovementResponse(BaseModel):
    """Response schema for ranking movement."""
    user_id: int
    user_name: str
    previous_rank: Optional[int]
    current_rank: int
    change: int  # positive = improved, negative = dropped
    is_new_leader: bool = False
    total_position_points: int
    group_number: int


class TSFRankingUpdateResponse(BaseModel):
    """Response schema for ranking update."""
    message: str
    day_number: int
    leg_number: Optional[int] = None
    movements: list[TSFRankingMovementResponse]
    current_leaders_by_group: dict[int, TSFRankingMovementResponse] = {}


# =============================================================================
# Generate Days/Legs Request
# =============================================================================

class TSFGenerateDaysRequest(BaseModel):
    """Request schema for generating TSF days and legs."""
    start_date: Optional[date] = None
    leg_duration_minutes: int = Field(default=30, ge=10, le=120)
    break_between_legs_minutes: int = Field(default=15, ge=0, le=60)


class TSFGenerateDaysResponse(BaseModel):
    """Response schema for generated days and legs."""
    message: str
    days_created: int
    legs_per_day: int
    total_legs: int
    days: list[TSFDayResponse]


# =============================================================================
# Calculate Standings Request
# =============================================================================

class TSFCalculateStandingsRequest(BaseModel):
    """Request schema for calculating standings."""
    day_number: Optional[int] = None  # If None, calculate final standings
    recalculate: bool = False  # Force recalculation even if already exists


class TSFCalculateStandingsResponse(BaseModel):
    """Response schema for calculated standings."""
    message: str
    standings_type: str  # "day" or "final"
    day_number: Optional[int] = None
    participants_ranked: int


# =============================================================================
# Sector Validator Schemas
# =============================================================================

class TSFSectorValidatorBase(BaseModel):
    """Base schema for TSF sector validator."""
    sector_number: int = Field(..., ge=1, description="Sector number (1-indexed)")
    is_active: bool = Field(default=True)


class TSFSectorValidatorCreate(TSFSectorValidatorBase):
    """Create schema for TSF sector validator."""
    validator_id: int = Field(..., description="User ID of the validator")
    backup_validator_id: Optional[int] = Field(default=None, description="Backup validator user ID")


class TSFSectorValidatorUpdate(BaseModel):
    """Update schema for TSF sector validator."""
    validator_id: Optional[int] = None
    backup_validator_id: Optional[int] = None
    is_active: Optional[bool] = None


class TSFSectorValidatorResponse(TSFSectorValidatorBase):
    """Response schema for TSF sector validator."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    validator_id: int
    backup_validator_id: Optional[int]
    created_at: datetime

    # Nested user info
    validator_name: Optional[str] = None
    validator_email: Optional[str] = None
    validator_avatar: Optional[str] = None
    backup_validator_name: Optional[str] = None


class TSFSectorValidatorListResponse(BaseModel):
    """Response schema for listing sector validators."""
    items: list[TSFSectorValidatorResponse]
    total: int
    total_sectors: int
    assigned_sectors: int


# =============================================================================
# Enhanced Ranking Schemas (Leg-by-Leg with RANK.AVG)
# =============================================================================

class TSFLegPositionDetail(BaseModel):
    """Detailed position for a single leg."""
    leg_number: int
    fish_caught: int
    leg_points: float  # RANK.AVG calculated position points


class TSFParticipantLegRanking(BaseModel):
    """Participant ranking within a group with leg breakdown."""
    user_id: int
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None
    is_ghost: bool = False
    legs: list[TSFLegPositionDetail]
    total_fish: int
    total_points: float  # Sum of leg_points
    group_rank: float  # RANK.AVG style (e.g., 1.5 for tied positions)


class TSFGroupRankingResponse(BaseModel):
    """Response schema for group/sector ranking with leg details."""
    group_number: int
    participants: list[TSFParticipantLegRanking]


class TSFGroupRankingListResponse(BaseModel):
    """Response schema for all groups ranking."""
    event_id: int
    day_number: Optional[int] = None  # None = final ranking
    groups: list[TSFGroupRankingResponse]
    total_groups: int
    total_participants: int


class TSFLegRankingEntry(BaseModel):
    """Single entry in leg ranking with RANK.AVG."""
    user_id: int
    user_name: Optional[str] = None
    group_number: int
    fish_caught: int
    leg_points: float  # RANK.AVG position
    seat_index: int


class TSFLegRankingResponse(BaseModel):
    """Response schema for single leg ranking (with RANK.AVG)."""
    event_id: int
    day_number: int
    leg_number: int
    group_number: int
    rankings: list[TSFLegRankingEntry]
    total_in_group: int


class TSFFinalRankingEntry(BaseModel):
    """Entry in final ranking."""
    user_id: int
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None
    is_ghost: bool = False
    final_rank: int
    total_points: float  # Lower is better
    total_fish_caught: int
    group_number: int
    # Day breakdown
    day_totals: dict[str, float] = {}  # day_number -> points


class TSFFinalRankingResponse(BaseModel):
    """Response schema for final event ranking."""
    event_id: int
    rankings: list[TSFFinalRankingEntry]
    total_participants: int
    total_days: int
    completed_days: int


class TSFBestPerformer(BaseModel):
    """Best performer stats."""
    user_id: int
    user_name: Optional[str] = None
    value: float  # points or fish count depending on context


class TSFEventStatisticsResponse(BaseModel):
    """Event-level statistics for TSF competition."""
    event_id: int
    total_participants: int
    total_groups: int
    total_days: int
    completed_days: int
    total_legs_completed: int
    total_fish_caught: int
    # Best performers
    best_group_by_points: Optional[int] = None  # group_number
    best_group_points_value: Optional[float] = None
    best_group_by_fish: Optional[int] = None  # group_number
    best_group_fish_value: Optional[int] = None
    best_participant_by_points: Optional[TSFBestPerformer] = None
    best_participant_by_fish: Optional[TSFBestPerformer] = None
