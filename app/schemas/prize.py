"""Prize schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class PrizeBase(BaseModel):
    """Base prize schema."""

    place: int = Field(..., ge=1, description="Placement position (1st, 2nd, 3rd, etc.)")
    title: str = Field(..., min_length=1, max_length=100, description="Prize title")
    description: Optional[str] = Field(None, max_length=1000, description="Prize description")
    value: Optional[str] = Field(None, max_length=200, description="Prize value (free text, e.g., '500 RON', 'Fishing Rod')")
    image_url: Optional[str] = Field(None, max_length=500, description="Prize image URL")


class PrizeCreate(PrizeBase):
    """Schema for creating a prize."""

    pass


class PrizeUpdate(BaseModel):
    """Schema for updating a prize."""

    place: Optional[int] = Field(None, ge=1)
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=1000)
    value: Optional[str] = Field(None, max_length=200)
    image_url: Optional[str] = Field(None, max_length=500)


class PrizeResponse(BaseModel):
    """Schema for prize response."""

    id: int
    event_id: int
    place: int
    title: str
    description: Optional[str]
    value: Optional[str]
    image_url: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class PrizeBulkItem(PrizeBase):
    """Single item for bulk prize update."""

    id: Optional[int] = None  # None for new prizes, set for existing


class PrizeBulkUpdate(BaseModel):
    """Schema for bulk updating all prizes for an event."""

    prizes: List[PrizeBulkItem] = Field(..., description="List of prizes to set")


class PrizeListResponse(BaseModel):
    """Response for list of prizes."""

    items: List[PrizeResponse]
    total: int
