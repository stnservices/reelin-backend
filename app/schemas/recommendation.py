"""Recommendation schemas for API responses."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


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


class EventRecommendation(BaseModel):
    """A single event recommendation with scoring info."""

    event: EventSummary
    score: float = Field(description="Recommendation score (0-100)")
    reasons: list[str] = Field(description="Why this event is recommended")
    friends_enrolled: Optional[list[UserSummary]] = Field(
        None, description="Friends joining this event (Pro only)"
    )


class AnglerRecommendation(BaseModel):
    """A single angler recommendation with scoring info."""

    user: UserSummary
    score: float = Field(description="Recommendation score (0-100)")
    reasons: list[str] = Field(description="Why this angler is recommended")
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
