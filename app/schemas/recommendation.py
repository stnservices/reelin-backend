"""Recommendation schemas for API responses."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ReasonItem(BaseModel):
    """A translatable reason with key and optional parameters."""

    key: str = Field(description="Translation key (e.g., 'near_you', 'event_type_match')")
    args: Optional[dict[str, Any]] = Field(
        None, description="Parameters for the translation (e.g., {'distance': 10})"
    )


class UserSummary(BaseModel):
    """Minimal user info for recommendations."""

    id: int
    name: str
    profile_picture_url: Optional[str] = None


class EventSummary(BaseModel):
    """Minimal event info for recommendations."""

    id: int
    name: str
    slug: str
    start_date: datetime
    end_date: datetime
    location_name: Optional[str] = None
    cover_image_url: Optional[str] = None
    event_type_name: Optional[str] = None


class MLInsights(BaseModel):
    """ML model insights for a recommendation."""

    confidence: float = Field(ge=0.0, le=1.0, description="ML confidence score (0-1)")
    confidence_label: str = Field(description="Human-readable confidence level")
    factors: list[ReasonItem] = Field(description="Key factors influencing the prediction")


class EventRecommendation(BaseModel):
    """A single event recommendation with scoring info."""

    event: EventSummary
    score: float = Field(description="Recommendation score (0-100)")
    reasons: list[ReasonItem] = Field(description="Why this event is recommended")
    friends_enrolled: Optional[list[UserSummary]] = Field(
        None, description="Friends joining this event (Pro only)"
    )
    ml_insights: Optional[MLInsights] = Field(
        None, description="ML-based insights (when ML is active)"
    )


class AnglerRecommendation(BaseModel):
    """A single angler recommendation with scoring info."""

    user: UserSummary
    score: float = Field(description="Recommendation score (0-100)")
    reasons: list[ReasonItem] = Field(description="Why this angler is recommended")
    mutual_friends: Optional[list[UserSummary]] = Field(
        None, description="Mutual connections (Pro only)"
    )


class EventRecommendationsResponse(BaseModel):
    """Response for event recommendations endpoint."""

    recommendations: list[EventRecommendation]
    is_pro: bool = Field(description="Whether user has Pro access")
    total_available: int = Field(
        description="Total recommendations available (may be limited for free users)"
    )


class AnglerRecommendationsResponse(BaseModel):
    """Response for angler recommendations endpoint."""

    recommendations: list[AnglerRecommendation]
    is_pro: bool = Field(description="Whether user has Pro access")
    total_available: int = Field(
        description="Total recommendations available (may be limited for free users)"
    )


class DismissRequest(BaseModel):
    """Request to dismiss a recommendation."""

    item_type: Literal["event", "angler"] = Field(
        description="Type of item to dismiss"
    )
    item_id: int = Field(description="ID of the item to dismiss")


class DismissResponse(BaseModel):
    """Response for dismiss endpoint."""

    status: str = "dismissed"


# ---- Completed Event Insights ----


class CompletedEventSummary(BaseModel):
    """Minimal event info for completed event insights."""

    id: int
    name: str
    slug: str
    start_date: datetime
    end_date: datetime
    event_type_name: Optional[str] = None


class CompletedEventInsight(BaseModel):
    """ML match insight for a completed event."""

    event: CompletedEventSummary
    match_score: int = Field(description="Match percentage (0-100)")
    match_label: str = Field(description="Human-readable match level")
    factors: list[str] = Field(description="Why you were a good match")


class CompletedEventInsightsResponse(BaseModel):
    """Response for completed event insights endpoint."""

    insights: list[CompletedEventInsight]
    total: int = Field(description="Total completed events analyzed")
