"""Waypoint schemas for API requests/responses."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class WaypointIconResponse(BaseModel):
    """Response schema for waypoint icons."""

    id: int
    code: str
    name: str
    emoji: Optional[str] = None
    svg_url: Optional[str] = None
    is_pro_only: bool = False

    class Config:
        from_attributes = True


class WaypointCategoryResponse(BaseModel):
    """Response schema for waypoint categories."""

    id: int
    code: str
    name: str
    color: str

    class Config:
        from_attributes = True


class WaypointCreate(BaseModel):
    """Schema for creating a new waypoint."""

    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    icon: str = Field(default="pin", max_length=50)
    color: str = Field(default="#E85D04", max_length=7)
    category: Optional[str] = Field(None, max_length=50)


class WaypointUpdate(BaseModel):
    """Schema for updating a waypoint."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=7)
    category: Optional[str] = Field(None, max_length=50)
    # Location cannot be updated - delete and create new instead


class WaypointResponse(BaseModel):
    """Response schema for a waypoint."""

    id: int
    latitude: float
    longitude: float
    name: str
    description: Optional[str] = None
    icon: str
    color: str
    category: Optional[str] = None
    photo_url: Optional[str] = None
    is_shared: bool = False
    shared_with_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WaypointListResponse(BaseModel):
    """Paginated list of waypoints."""

    items: List[WaypointResponse]
    total: int
    page: int
    page_size: int
    pages: int


class WaypointShareRequest(BaseModel):
    """Request to share a waypoint with users."""

    user_ids: List[int] = Field(..., min_length=1)


class SharedWaypointUser(BaseModel):
    """User in share suggestions or shared list."""

    id: int
    name: str
    avatar_url: Optional[str] = None
    already_shared: bool = False


class WaypointShareResponse(BaseModel):
    """Response after sharing a waypoint."""

    shared_with: List[SharedWaypointUser]
    total_shared: int


class SharedWaypointResponse(BaseModel):
    """A waypoint shared with the current user."""

    id: int
    latitude: float
    longitude: float
    name: str
    description: Optional[str] = None
    icon: str
    color: str
    category: Optional[str] = None
    photo_url: Optional[str] = None
    # Owner info
    owner_id: int
    owner_name: str
    owner_avatar_url: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class WaypointConfigResponse(BaseModel):
    """Configuration data for waypoints (icons, categories, limits)."""

    icons: List[WaypointIconResponse]
    categories: List[WaypointCategoryResponse]
    free_limit: int = 3
    is_pro: bool = False
    current_count: int = 0
    can_add_more: bool = True
