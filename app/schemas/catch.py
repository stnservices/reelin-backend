"""Catch schemas for request/response validation."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.catch import CatchStatus
from app.schemas.ai_analysis import AiAnalysisResponse, build_ai_analysis_response


class CatchCreate(BaseModel):
    """Schema for submitting a new catch."""

    event_id: int
    fish_id: int
    length: float = Field(..., gt=0, le=400, description="Length in cm (max 400)")
    weight: Optional[float] = Field(None, gt=0, le=500, description="Weight in kg (max 500)")
    photo_url: str
    location_lat: Optional[float] = Field(None, ge=-90, le=90)
    location_lng: Optional[float] = Field(None, ge=-180, le=180)
    catch_time: Optional[datetime] = None
    # Proxy upload: organizer/validator uploads on behalf of an angler
    on_behalf_of_user_id: Optional[int] = Field(
        None,
        description="User ID of the angler (for proxy uploads by organizers/validators)"
    )


class CatchValidation(BaseModel):
    """Schema for validating a catch."""

    status: CatchStatus
    rejection_reason: Optional[str] = None
    adjusted_length: Optional[float] = Field(None, gt=0, description="Corrected length if needed")
    adjusted_weight: Optional[float] = Field(None, gt=0, description="Corrected weight if needed")
    new_fish_id: Optional[int] = Field(None, description="Corrected fish species ID if angler selected wrong fish")


class CatchRevalidation(BaseModel):
    """Schema for revalidating an already validated catch."""

    new_status: CatchStatus
    reason: str = Field(..., min_length=5, description="Reason for revalidation (required)")
    adjusted_length: Optional[float] = Field(None, gt=0, description="Corrected length if approving")
    adjusted_weight: Optional[float] = Field(None, gt=0, description="Corrected weight if approving")
    new_fish_id: Optional[int] = Field(None, description="Corrected fish species ID if angler selected wrong fish")


class FishResponse(BaseModel):
    """Fish info in catch response."""

    id: int
    name: str
    name_ro: Optional[str] = None
    scientific_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class CatchResponse(BaseModel):
    """Schema for catch response."""

    id: int
    event_id: int
    user_id: int
    fish_id: int
    length: float
    weight: Optional[float] = None
    photo_url: str
    thumbnail_url: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    location_accuracy: Optional[float] = None
    points: Optional[float] = None
    status: str
    submitted_at: datetime
    catch_time: Optional[datetime] = None
    validated_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode='before')
    @classmethod
    def set_thumbnail_from_poster(cls, data):
        """Use poster_url (video thumbnail) as fallback for thumbnail_url."""
        # Handle both dict and ORM model cases
        if hasattr(data, '__dict__'):
            # ORM model case
            poster_url = getattr(data, 'poster_url', None)
            thumbnail_url = getattr(data, 'thumbnail_url', None)
            if poster_url and not thumbnail_url:
                # Create a dict copy to modify
                data_dict = {k: getattr(data, k) for k in cls.model_fields.keys() if hasattr(data, k)}
                data_dict['thumbnail_url'] = poster_url
                return data_dict
        elif isinstance(data, dict):
            # Dict case
            if data.get('poster_url') and not data.get('thumbnail_url'):
                data['thumbnail_url'] = data['poster_url']
        return data


class CatchDetailResponse(CatchResponse):
    """Detailed catch response with fish and user info."""

    fish_name: Optional[str] = None
    fish_name_ro: Optional[str] = None
    fish_slug: Optional[str] = None
    user_email: Optional[str] = None
    user_first_name: Optional[str] = None
    user_last_name: Optional[str] = None
    user_phone: Optional[str] = None
    validated_by_email: Optional[str] = None
    # Revalidation info
    revalidated_at: Optional[datetime] = None
    revalidated_by_email: Optional[str] = None
    revalidation_reason: Optional[str] = None
    # Proxy upload info
    uploaded_by_id: Optional[int] = None
    uploaded_by_email: Optional[str] = None
    uploaded_by_first_name: Optional[str] = None
    uploaded_by_last_name: Optional[str] = None
    is_proxy_upload: bool = False
    # Team info (for team events)
    team_id: Optional[int] = None
    team_name: Optional[str] = None
    # Enrollment info
    enrollment_number: Optional[int] = None
    draw_number: Optional[int] = None
    # AI Analysis (for validators only)
    ai_analysis: Optional[AiAnalysisResponse] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_catch(
        cls,
        catch,
        team_id: int = None,
        team_name: str = None,
        enrollment_number: int = None,
        draw_number: int = None,
        include_ai_analysis: bool = False,
    ) -> "CatchDetailResponse":
        """Create response from catch model with nested relationships."""
        # Determine if proxy upload
        is_proxy = catch.is_proxy_upload if hasattr(catch, 'is_proxy_upload') else False

        return cls(
            id=catch.id,
            event_id=catch.event_id,
            user_id=catch.user_id,
            fish_id=catch.fish_id,
            length=catch.length,
            weight=catch.weight,
            photo_url=catch.photo_url,
            thumbnail_url=catch.poster_url or catch.thumbnail_url,  # poster_url for videos, thumbnail_url for images
            location_lat=catch.location_lat,
            location_lng=catch.location_lng,
            location_accuracy=catch.location_accuracy,
            points=catch.points,
            status=catch.status,
            submitted_at=catch.submitted_at,
            catch_time=catch.catch_time,
            validated_at=catch.validated_at,
            rejection_reason=catch.rejection_reason,
            fish_name=catch.fish.name if catch.fish else None,
            fish_name_ro=catch.fish.name_ro if catch.fish else None,
            fish_slug=catch.fish.slug if catch.fish else None,
            user_email=catch.user.email if catch.user else None,
            user_first_name=catch.user.profile.first_name if catch.user and catch.user.profile else None,
            user_last_name=catch.user.profile.last_name if catch.user and catch.user.profile else None,
            user_phone=catch.user.profile.phone if catch.user and catch.user.profile else None,
            validated_by_email=catch.validated_by.email if catch.validated_by else None,
            revalidated_at=catch.revalidated_at if hasattr(catch, 'revalidated_at') else None,
            revalidated_by_email=catch.revalidated_by.email if hasattr(catch, 'revalidated_by') and catch.revalidated_by else None,
            revalidation_reason=catch.revalidation_reason if hasattr(catch, 'revalidation_reason') else None,
            # Proxy upload info
            uploaded_by_id=catch.uploaded_by_id if hasattr(catch, 'uploaded_by_id') else None,
            uploaded_by_email=catch.uploaded_by.email if hasattr(catch, 'uploaded_by') and catch.uploaded_by else None,
            uploaded_by_first_name=catch.uploaded_by.profile.first_name if hasattr(catch, 'uploaded_by') and catch.uploaded_by and catch.uploaded_by.profile else None,
            uploaded_by_last_name=catch.uploaded_by.profile.last_name if hasattr(catch, 'uploaded_by') and catch.uploaded_by and catch.uploaded_by.profile else None,
            is_proxy_upload=is_proxy,
            team_id=team_id,
            team_name=team_name,
            enrollment_number=enrollment_number,
            draw_number=draw_number,
            ai_analysis=build_ai_analysis_response(catch.ai_analysis) if include_ai_analysis and hasattr(catch, 'ai_analysis') else None,
        )


class CatchListResponse(BaseModel):
    """Paginated catch list response."""

    items: list[CatchDetailResponse]
    total: int
    page: int
    page_size: int
    pages: int


class ScoreboardEntry(BaseModel):
    """Single scoreboard entry."""

    rank: int
    user_id: int
    user_email: Optional[str] = None
    user_first_name: Optional[str] = None
    user_last_name: Optional[str] = None
    total_catches: int
    total_length: float
    total_weight: Optional[float] = None
    total_points: float
    best_catch_length: Optional[float] = None
    previous_rank: Optional[int] = None
    rank_change: Optional[int] = None  # Positive = moved up

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_scoreboard(cls, scoreboard) -> "ScoreboardEntry":
        """Create entry from scoreboard model."""
        rank_change = None
        if scoreboard.previous_rank is not None:
            rank_change = scoreboard.previous_rank - scoreboard.rank

        return cls(
            rank=scoreboard.rank,
            user_id=scoreboard.user_id,
            user_email=scoreboard.user.email if scoreboard.user else None,
            user_first_name=scoreboard.user.profile.first_name if scoreboard.user and scoreboard.user.profile else None,
            user_last_name=scoreboard.user.profile.last_name if scoreboard.user and scoreboard.user.profile else None,
            total_catches=scoreboard.total_catches,
            total_length=scoreboard.total_length,
            total_weight=scoreboard.total_weight,
            total_points=scoreboard.total_points,
            best_catch_length=scoreboard.best_catch_length,
            previous_rank=scoreboard.previous_rank,
            rank_change=rank_change,
        )


class LeaderboardResponse(BaseModel):
    """Event leaderboard response."""

    event_id: int
    entries: list[ScoreboardEntry]
    total_participants: int
    last_updated: Optional[datetime] = None


class CatchSearchItem(BaseModel):
    """Search result item for catch revalidation."""

    id: int
    user_id: int
    user_name: str
    user_email: str
    team_id: Optional[int] = None
    team_name: Optional[str] = None
    fish_id: int
    fish_name: str
    fish_name_ro: Optional[str] = None
    length: float
    weight: Optional[float] = None
    photo_url: str
    status: str
    validated_at: Optional[datetime] = None
    validated_by: Optional[str] = None
    revalidated_at: Optional[datetime] = None
    revalidated_by: Optional[str] = None
    revalidation_reason: Optional[str] = None


class CatchSearchResponse(BaseModel):
    """Paginated catch search response for revalidation."""

    items: list[CatchSearchItem]
    total: int
    page: int
    page_size: int
    pages: int


# ============== Catch Map Schemas ==============


class CatchMapItem(BaseModel):
    """Single catch item for map display."""

    catch_id: int
    lat: float
    lng: float
    species_id: int
    species_name: str
    species_name_ro: Optional[str] = None
    species_icon: Optional[str] = None  # Icon URL or slug
    user_id: int
    user_name: str
    length_cm: float
    weight_kg: Optional[float] = None
    photo_thumbnail_url: Optional[str] = None
    caught_at: datetime
    points: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_catch(cls, catch) -> "CatchMapItem":
        """Create map item from catch model."""
        user_name = "Unknown"
        if catch.user and catch.user.profile:
            first = catch.user.profile.first_name or ""
            last = catch.user.profile.last_name or ""
            user_name = f"{first} {last}".strip() or catch.user.email.split("@")[0]

        return cls(
            catch_id=catch.id,
            lat=catch.location_lat,
            lng=catch.location_lng,
            species_id=catch.fish_id,
            species_name=catch.fish.name if catch.fish else "Unknown",
            species_name_ro=catch.fish.name_ro if catch.fish else None,
            species_icon=catch.fish.slug if catch.fish else None,
            user_id=catch.user_id,
            user_name=user_name,
            length_cm=catch.length,
            weight_kg=catch.weight,
            photo_thumbnail_url=catch.poster_url or catch.thumbnail_url or catch.photo_url,
            caught_at=catch.catch_time or catch.submitted_at,
            points=catch.points,
        )


class ClusterHint(BaseModel):
    """Clustering hint for map optimization."""

    center_lat: float
    center_lng: float
    count: int
    bounds: Optional[dict] = None  # {north, south, east, west}


class CatchMapResponse(BaseModel):
    """Catch map data response."""

    event_id: int
    catches: list[CatchMapItem]
    total_catches: int
    species_filter: Optional[int] = None
    user_filter: Optional[int] = None
    is_pro_user: bool
    showing_own_catches_only: bool
    cluster_hints: Optional[list[ClusterHint]] = None


# ============== Catch Reaction Schemas ==============


class CatchReactionRequest(BaseModel):
    """Request to add/change a reaction on a catch."""

    reaction_type: str = Field(..., pattern="^(like|dislike)$", description="Either 'like' or 'dislike'")


class CatchReactionCounts(BaseModel):
    """Reaction counts for a catch."""

    likes: int = 0
    dislikes: int = 0
    user_reaction: Optional[str] = None  # 'like', 'dislike', or None


class CatchReactionResponse(BaseModel):
    """Response after adding/changing a reaction."""

    catch_id: int
    reaction_type: str
    counts: CatchReactionCounts
