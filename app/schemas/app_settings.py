"""Schemas for app settings."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AppSettingsResponse(BaseModel):
    """Response schema for app settings."""

    app_version: str
    app_min_version_ios: str
    app_min_version_android: str
    app_store_url: str
    play_store_url: str
    release_notes: Optional[str] = None
    force_update_message: Optional[str] = None
    updated_at: datetime
    updated_by_id: Optional[int] = None

    model_config = {"from_attributes": True}


class AppSettingsUpdate(BaseModel):
    """Schema for updating app settings."""

    app_version: Optional[str] = Field(
        None,
        min_length=1,
        max_length=20,
        pattern=r"^\d+\.\d+\.\d+$",
        description="Latest app version (semantic versioning, e.g., 1.2.3)"
    )
    app_min_version_ios: Optional[str] = Field(
        None,
        min_length=1,
        max_length=20,
        pattern=r"^\d+\.\d+\.\d+$",
        description="Minimum required iOS version"
    )
    app_min_version_android: Optional[str] = Field(
        None,
        min_length=1,
        max_length=20,
        pattern=r"^\d+\.\d+\.\d+$",
        description="Minimum required Android version"
    )
    app_store_url: Optional[str] = Field(
        None,
        max_length=500,
        description="iOS App Store URL"
    )
    play_store_url: Optional[str] = Field(
        None,
        max_length=500,
        description="Google Play Store URL"
    )
    release_notes: Optional[str] = Field(
        None,
        max_length=2000,
        description="Release notes shown in update dialog"
    )
    force_update_message: Optional[str] = Field(
        None,
        max_length=500,
        description="Message shown when force update is required"
    )
