"""Event-related Pydantic schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.event import EventStatus
from app.schemas.billing import BillingProfileBrief
from app.schemas.currency import CurrencyResponse


# Location schemas
class CountryResponse(BaseModel):
    """Schema for country response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str


class CityResponse(BaseModel):
    """Schema for city response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    country: Optional[CountryResponse] = None


class LocationResponse(BaseModel):
    """Schema for fishing spot location response with city and country."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    city: Optional[CityResponse] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


# Sponsor schemas
class SponsorBriefResponse(BaseModel):
    """Brief sponsor info for embedding in event responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    logo_url: Optional[str] = None
    website_url: Optional[str] = None
    display_order: int = 0


class EventTypeResponse(BaseModel):
    """Schema for event type response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    format_code: str  # sf, ta - determines which wizard/format to use
    description: Optional[str] = None
    icon_url: Optional[str] = None
    is_active: bool


class ScoringConfigResponse(BaseModel):
    """Schema for scoring configuration response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    format_code: str  # sf, ta
    description: Optional[str] = None
    calculation_info: Optional[str] = None  # Detailed scoring explanation
    team_scoring_info: Optional[str] = None  # Team scoring explanation
    default_top_x: Optional[int] = None
    default_catch_slots: Optional[int] = None
    rules: Dict[str, Any] = {}
    is_active: bool
    event_types: List[EventTypeResponse] = []


class EventPrizeCreate(BaseModel):
    """Schema for creating event prize."""

    place: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    value: Optional[float] = Field(None, ge=0)


class EventPrizeResponse(BaseModel):
    """Schema for event prize response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    place: int
    title: str
    description: Optional[str] = None
    value: Optional[float] = None
    image_url: Optional[str] = None


class EventScoringRuleCreate(BaseModel):
    """Schema for creating event scoring rule."""

    fish_id: Optional[int] = None
    min_length: Optional[float] = Field(None, ge=0)
    max_length: Optional[float] = Field(None, ge=0)
    points_per_cm: Optional[float] = None
    bonus_points: Optional[float] = None
    points_formula: Optional[Dict[str, Any]] = None


class EventScoringRuleResponse(BaseModel):
    """Schema for event scoring rule response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    fish_id: Optional[int] = None
    min_length: Optional[float] = None
    max_length: Optional[float] = None
    points_per_cm: Optional[float] = None
    bonus_points: Optional[float] = None
    points_formula: Optional[Dict[str, Any]] = None


class FishResponse(BaseModel):
    """Schema for fish response (used in nested responses)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    name_en: Optional[str] = None  # English translation
    name_ro: Optional[str] = None  # Romanian translation
    scientific_name: Optional[str] = None
    min_length: Optional[float] = None
    max_length: Optional[float] = None
    image_url: Optional[str] = None


class EventFishScoringCreate(BaseModel):
    """Schema for creating event fish scoring configuration."""

    fish_id: int
    accountable_catch_slots: int = Field(5, ge=1)
    accountable_min_length: float = Field(0.0, ge=0)
    under_min_length_points: int = Field(0, ge=0)
    top_x_catches: Optional[int] = Field(None, ge=1)
    display_order: int = 0


class EventFishScoringUpdate(BaseModel):
    """Schema for updating event fish scoring configuration."""

    accountable_catch_slots: Optional[int] = Field(None, ge=1)
    accountable_min_length: Optional[float] = Field(None, ge=0)
    under_min_length_points: Optional[int] = Field(None, ge=0)
    top_x_catches: Optional[int] = Field(None, ge=1)
    display_order: Optional[int] = None


class EventFishScoringResponse(BaseModel):
    """Schema for event fish scoring response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    fish_id: int
    fish: Optional[FishResponse] = None
    accountable_catch_slots: int
    accountable_min_length: float
    under_min_length_points: int
    top_x_catches: Optional[int] = None
    display_order: int
    created_at: datetime
    updated_at: datetime


class EventSpeciesBonusPointsCreate(BaseModel):
    """Schema for creating species bonus points configuration."""

    species_count: int = Field(..., ge=2)
    bonus_points: int = Field(..., ge=1)


class EventSpeciesBonusPointsResponse(BaseModel):
    """Schema for species bonus points response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    species_count: int
    bonus_points: int
    created_at: datetime


class EventCreate(BaseModel):
    """Schema for creating an event."""

    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    event_type_id: int
    scoring_config_id: int
    start_date: datetime
    end_date: datetime
    registration_deadline: Optional[datetime] = None
    location_id: Optional[int] = None
    location_name: Optional[str] = Field(None, max_length=200)
    # Meeting point (mandatory for publishing, optional during draft)
    meeting_point_lat: Optional[float] = Field(None, ge=-90, le=90)
    meeting_point_lng: Optional[float] = Field(None, ge=-180, le=180)
    meeting_point_address: Optional[str] = Field(None, max_length=500)
    max_participants: Optional[int] = Field(None, ge=1)
    requires_approval: bool = True
    top_x_overall: Optional[int] = Field(None, ge=1)  # For top_x_overall scoring
    has_bonus_points: bool = True
    # Event classification flags
    is_team_event: bool = False
    is_national_event: bool = False
    is_tournament_event: bool = False
    # Team settings (min 1 allows solo teams - constraint checked at event start)
    min_team_size: Optional[int] = Field(None, ge=1)
    max_team_size: Optional[int] = Field(None, ge=1)
    rule_id: Optional[int] = None  # Organizer rule to apply
    rules: Optional[str] = None  # Custom/additional rules text
    # Participation fee (informational - payment handled offline)
    participation_fee: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    participation_fee_currency_id: Optional[int] = None
    # Media upload constraints
    allow_gallery_upload: bool = True
    allowed_media_type: str = Field("both", pattern="^(image|video|both)$")
    max_video_duration: Optional[int] = Field(None, ge=1)
    # AI Analysis settings
    use_ai_analysis: bool = False  # Enable Google Vision analysis
    use_ml_auto_validation: bool = False  # Auto-approve if confidence met
    ml_confidence_threshold: float = Field(0.85, ge=0.0, le=1.0)
    # Test event flag - test events are excluded from stats, achievements, rankings
    is_test: bool = False
    prizes: Optional[List[EventPrizeCreate]] = None
    scoring_rules: Optional[List[EventScoringRuleCreate]] = None
    fish_scoring: Optional[List[EventFishScoringCreate]] = None  # Fish species config
    bonus_points: Optional[List[EventSpeciesBonusPointsCreate]] = None  # Species bonuses

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v: datetime, info) -> datetime:
        if "start_date" in info.data:
            start = info.data["start_date"]
            # Normalize timezone awareness for comparison
            # Strip tzinfo from both to compare naive datetimes
            v_naive = v.replace(tzinfo=None) if v.tzinfo else v
            start_naive = start.replace(tzinfo=None) if start.tzinfo else start
            if v_naive <= start_naive:
                raise ValueError("end_date must be after start_date")
        return v


class EventUpdate(BaseModel):
    """Schema for updating an event."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    event_type_id: Optional[int] = None
    scoring_config_id: Optional[int] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    registration_deadline: Optional[datetime] = None
    location_id: Optional[int] = None
    location_name: Optional[str] = Field(None, max_length=200)
    # Meeting point
    meeting_point_lat: Optional[float] = Field(None, ge=-90, le=90)
    meeting_point_lng: Optional[float] = Field(None, ge=-180, le=180)
    meeting_point_address: Optional[str] = Field(None, max_length=500)
    max_participants: Optional[int] = Field(None, ge=1)
    requires_approval: Optional[bool] = None
    top_x_overall: Optional[int] = Field(None, ge=1)
    has_bonus_points: Optional[bool] = None
    # Event classification flags
    is_team_event: Optional[bool] = None
    is_national_event: Optional[bool] = None
    is_tournament_event: Optional[bool] = None
    # Team settings (min 1 allows solo teams - constraint checked at event start)
    min_team_size: Optional[int] = Field(None, ge=1)
    max_team_size: Optional[int] = Field(None, ge=1)
    rule_id: Optional[int] = None  # Organizer rule to apply
    rules: Optional[str] = None  # Custom/additional rules text
    # Participation fee (informational - payment handled offline)
    participation_fee: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    participation_fee_currency_id: Optional[int] = None
    # Media upload constraints
    allow_gallery_upload: Optional[bool] = None
    allowed_media_type: Optional[str] = Field(None, pattern="^(image|video|both)$")
    max_video_duration: Optional[int] = Field(None, ge=1)
    # AI Analysis settings
    use_ai_analysis: Optional[bool] = None
    use_ml_auto_validation: Optional[bool] = None
    ml_confidence_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    # Test event flag - test events are excluded from stats, achievements, rankings
    is_test: Optional[bool] = None
    image_url: Optional[str] = None
    status: Optional[EventStatus] = None


class ForceStatusChangeRequest(BaseModel):
    """Schema for force-changing event status (admin/organizer override)."""

    target_status: EventStatus
    reason: str = Field(..., min_length=10, max_length=500)


class EventStatusUpdateRequest(BaseModel):
    """Schema for unified event status updates."""

    action: str = Field(
        ...,
        pattern="^(publish|recall|start|stop|cancel|delete|restore)$",
        description="Status action to perform",
    )
    reason: Optional[str] = Field(None, min_length=10, max_length=500)
    force: bool = Field(
        False,
        description="Bypass normal transition rules (owner/admin only, requires reason)",
    )

    @field_validator("reason")
    @classmethod
    def reason_required_for_cancel_and_force(
        cls, v: Optional[str], info
    ) -> Optional[str]:
        action = info.data.get("action")
        force = info.data.get("force", False)
        if (action == "cancel" or force) and not v:
            raise ValueError("Reason is required for cancel action or force mode")
        return v


class EventStatusUpdateResponse(BaseModel):
    """Response for unified event status updates."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    is_deleted: bool
    previous_status: str
    action_performed: str
    published_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None


class RuleBriefResponse(BaseModel):
    """Brief rule info for embedding in event responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    content: Optional[str] = None
    external_url: Optional[str] = None
    document_url: Optional[str] = None


class OrganizerInfo(BaseModel):
    """Schema for event organizer info."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    @property
    def full_name(self) -> str:
        """Return full name or email as fallback."""
        if self.first_name or self.last_name:
            return f"{self.first_name or ''} {self.last_name or ''}".strip()
        return self.email


class EventResponse(BaseModel):
    """Schema for event response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: Optional[str] = None
    event_type: EventTypeResponse
    scoring_config: ScoringConfigResponse
    start_date: datetime
    end_date: datetime
    registration_deadline: Optional[datetime] = None
    location_name: Optional[str] = None
    location: Optional[LocationResponse] = None
    # Meeting point
    meeting_point_lat: Optional[float] = None
    meeting_point_lng: Optional[float] = None
    meeting_point_address: Optional[str] = None
    status: str
    max_participants: Optional[int] = None
    requires_approval: bool
    top_x_overall: Optional[int] = None
    has_bonus_points: bool = True
    # Event classification flags
    is_team_event: bool = False
    is_national_event: bool = False
    is_tournament_event: bool = False
    # Team settings
    min_team_size: Optional[int] = None
    max_team_size: Optional[int] = None
    rule_id: Optional[int] = None
    rule: Optional[RuleBriefResponse] = None
    rules: Optional[str] = None  # Custom/additional rules text
    # Participation fee
    participation_fee: Optional[Decimal] = None
    participation_fee_currency: Optional[CurrencyResponse] = None
    # Media upload constraints
    allow_gallery_upload: bool = True
    allowed_media_type: str = "both"
    max_video_duration: Optional[int] = None
    # AI Analysis settings
    use_ai_analysis: bool = False
    use_ml_auto_validation: bool = False
    ml_confidence_threshold: float = 0.85
    # Test event flag
    is_test: bool = False
    image_url: Optional[str] = None
    created_at: datetime
    published_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_by_id: Optional[int] = None

    # Organizer info
    organizer: Optional[OrganizerInfo] = None

    # Organizer's club info (populated from Club where owner_id = created_by_id)
    organizer_club_name: Optional[str] = None
    organizer_club_logo_url: Optional[str] = None

    # Billing profile for this event (for invoicing)
    billing_profile: Optional[BillingProfileBrief] = None

    # Sponsors
    sponsors: List[SponsorBriefResponse] = []

    # Fish scoring configuration
    fish_scoring: List[EventFishScoringResponse] = []

    # Counts
    enrolled_count: Optional[int] = None
    approved_count: Optional[int] = None

    # Validator user IDs (for chat access check on mobile)
    validator_ids: List[int] = []


class EventListResponse(BaseModel):
    """Schema for event list item (lighter than full EventResponse)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    event_type: EventTypeResponse
    start_date: datetime
    end_date: datetime
    status: str
    location_name: Optional[str] = None
    image_url: Optional[str] = None
    is_team_event: bool = False
    is_national_event: bool = False
    is_tournament_event: bool = False
    is_test: bool = False
    enrolled_count: Optional[int] = None
    max_participants: Optional[int] = None


class PublishReadinessResponse(BaseModel):
    """Response schema for event publish readiness validation.

    Returns whether an event is ready to be published along with
    details about what validations passed or failed.
    """

    model_config = ConfigDict(from_attributes=True)

    is_ready: bool = Field(
        ...,
        description="True if all validations pass and event can be published"
    )
    missing_items: List[str] = Field(
        default_factory=list,
        description="List of i18n message keys for failed validations"
    )
    checks: Dict[str, bool] = Field(
        default_factory=dict,
        description="Individual check results (check_name -> pass/fail)"
    )
