"""Schemas for partners."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class PartnerBase(BaseModel):
    """Base schema for partner data."""

    name: str = Field(..., min_length=1, max_length=255, description="Partner name")
    logo_url: str = Field(..., max_length=500, description="URL to partner logo image")
    website_url: Optional[str] = Field(None, max_length=500, description="Partner website URL")
    display_order: int = Field(0, ge=0, description="Display order (lower = first)")
    is_active: bool = Field(True, description="Whether partner is visible on landing page")


class PartnerCreate(PartnerBase):
    """Schema for creating a partner."""

    pass


class PartnerUpdate(BaseModel):
    """Schema for updating a partner."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    logo_url: Optional[str] = Field(None, max_length=500)
    website_url: Optional[str] = Field(None, max_length=500)
    display_order: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None


class PartnerResponse(PartnerBase):
    """Response schema for partner."""

    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PartnerPublicResponse(BaseModel):
    """Public response schema for partner (limited fields)."""

    id: int
    name: str
    logo_url: str
    website_url: Optional[str] = None

    model_config = {"from_attributes": True}
