"""Schemas for Hall of Fame entries."""

from datetime import datetime
from typing import Optional, List, Any

from pydantic import BaseModel, Field, model_validator


class HallOfFameBase(BaseModel):
    """Base schema for Hall of Fame entry data."""

    athlete_name: str = Field(..., min_length=1, max_length=255, description="Name of the athlete")
    achievement_type: str = Field(
        ...,
        description="Type of achievement: world_champion, national_champion, world_podium, national_podium"
    )
    competition_name: str = Field(..., min_length=1, max_length=255, description="Name of the competition")
    competition_year: int = Field(..., ge=1900, le=2100, description="Year of the competition")
    position: Optional[int] = Field(None, ge=1, description="Finishing position (1, 2, 3, etc.)")
    format_code: Optional[str] = Field(None, pattern="^(sf|ta)$", description="Format: sf or ta")
    category: Optional[str] = Field(None, description="Category: individual, team, pairs")
    country: Optional[str] = Field(None, max_length=100, description="Country where competition was held")
    notes: Optional[str] = Field(None, description="Additional notes")
    image_url: Optional[str] = Field(None, max_length=500, description="URL to photo/certificate")
    athlete_avatar_url: Optional[str] = Field(None, max_length=500, description="URL to athlete avatar")


class HallOfFameCreate(HallOfFameBase):
    """Schema for creating a Hall of Fame entry."""

    user_id: Optional[int] = Field(None, description="Link to existing user account (optional)")


class HallOfFameUpdate(BaseModel):
    """Schema for updating a Hall of Fame entry."""

    user_id: Optional[int] = None
    athlete_name: Optional[str] = Field(None, min_length=1, max_length=255)
    achievement_type: Optional[str] = None
    competition_name: Optional[str] = Field(None, min_length=1, max_length=255)
    competition_year: Optional[int] = Field(None, ge=1900, le=2100)
    position: Optional[int] = Field(None, ge=1)
    format_code: Optional[str] = Field(None, pattern="^(sf|ta)$")
    category: Optional[str] = None
    country: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    image_url: Optional[str] = Field(None, max_length=500)
    athlete_avatar_url: Optional[str] = Field(None, max_length=500)


class HallOfFameUserInfo(BaseModel):
    """Nested user info for Hall of Fame response."""

    id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def extract_profile_fields(cls, data: Any) -> Any:
        """Extract first_name, last_name, avatar_url from user.profile if available."""
        if data is None:
            return data

        # If it's already a dict, return as-is
        if isinstance(data, dict):
            return data

        # If it's a UserAccount model, extract profile info
        result = {"id": getattr(data, "id", None)}

        profile = getattr(data, "profile", None)
        if profile:
            result["first_name"] = getattr(profile, "first_name", None)
            result["last_name"] = getattr(profile, "last_name", None)
            result["avatar_url"] = getattr(profile, "profile_picture_url", None)

        return result


class HallOfFameResponse(HallOfFameBase):
    """Response schema for Hall of Fame entry."""

    id: int
    user_id: Optional[int] = None
    user: Optional[HallOfFameUserInfo] = None
    created_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    # Computed fields
    display_name: str
    avatar_url: Optional[str] = None

    model_config = {"from_attributes": True}


class HallOfFamePublicResponse(BaseModel):
    """Public response schema for Hall of Fame entry (mobile app)."""

    id: int
    athlete_name: str
    display_name: str
    avatar_url: Optional[str] = None
    achievement_type: str
    competition_name: str
    competition_year: int
    position: Optional[int] = None
    format_code: Optional[str] = None
    category: Optional[str] = None
    country: Optional[str] = None
    image_url: Optional[str] = None

    model_config = {"from_attributes": True}


class HallOfFameGroupedResponse(BaseModel):
    """Grouped Hall of Fame response by achievement type."""

    world_champions: List[HallOfFamePublicResponse] = []
    national_champions: List[HallOfFamePublicResponse] = []
    world_podiums: List[HallOfFamePublicResponse] = []
    national_podiums: List[HallOfFamePublicResponse] = []


class HallOfFameListResponse(BaseModel):
    """Paginated list of Hall of Fame entries."""

    items: List[HallOfFameResponse]
    total: int
    page: int
    page_size: int
