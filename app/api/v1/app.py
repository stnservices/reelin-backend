"""Application metadata endpoints."""

from enum import Enum
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.app_settings import AppSettings
from app.config import get_settings

router = APIRouter()


class Platform(str, Enum):
    """Mobile platform types."""

    IOS = "ios"
    ANDROID = "android"


class VersionCheckResponse(BaseModel):
    """Response model for version check endpoint."""

    latest_version: str
    min_version: str
    current_version: str
    update_required: bool
    update_available: bool
    store_url: str
    release_notes: Optional[str] = None
    force_update_message: Optional[str] = None


def compare_versions(v1: str, v2: str) -> int:
    """
    Compare two semantic versions.

    Returns:
        -1 if v1 < v2
        0 if v1 == v2
        1 if v1 > v2
    """
    try:
        parts1 = [int(x) for x in v1.split(".")]
        parts2 = [int(x) for x in v2.split(".")]
    except ValueError:
        # If version parsing fails, assume no update needed
        return 0

    # Pad with zeros if needed
    while len(parts1) < 3:
        parts1.append(0)
    while len(parts2) < 3:
        parts2.append(0)

    for i in range(3):
        if parts1[i] < parts2[i]:
            return -1
        if parts1[i] > parts2[i]:
            return 1
    return 0


async def get_app_settings_from_db(db: AsyncSession) -> AppSettings:
    """Get app settings from database, creating default if not exists."""
    result = await db.execute(select(AppSettings).where(AppSettings.id == 1))
    settings = result.scalar_one_or_none()

    if not settings:
        # Create default settings
        settings = AppSettings(id=1)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

    return settings


@router.get("/version-check", response_model=VersionCheckResponse)
async def version_check(
    platform: Platform = Query(..., description="Mobile platform (ios or android)"),
    current_version: str = Query(..., description="Current app version installed"),
    db: AsyncSession = Depends(get_db),
) -> VersionCheckResponse:
    """
    Check if a mobile app update is available or required.

    This is a public endpoint that mobile apps call on startup to check
    if they need to update before continuing.

    - If `update_required` is true, the app should block usage until updated.
    - If `update_available` is true but not required, show an optional update prompt.
    """
    # Try to get settings from database first
    try:
        app_settings = await get_app_settings_from_db(db)
        latest_version = app_settings.app_version
        min_version = (
            app_settings.app_min_version_ios
            if platform == Platform.IOS
            else app_settings.app_min_version_android
        )
        store_url = (
            app_settings.app_store_url
            if platform == Platform.IOS
            else app_settings.play_store_url
        )
        release_notes = app_settings.release_notes
        force_update_message = app_settings.force_update_message
    except Exception:
        # Fallback to .env settings if database fails
        env_settings = get_settings()
        latest_version = env_settings.app_version
        min_version = (
            env_settings.app_min_version_ios
            if platform == Platform.IOS
            else env_settings.app_min_version_android
        )
        store_url = (
            env_settings.app_store_url
            if platform == Platform.IOS
            else env_settings.play_store_url
        )
        release_notes = None
        force_update_message = None

    # Determine update status
    update_required = compare_versions(current_version, min_version) < 0
    update_available = compare_versions(current_version, latest_version) < 0

    # Use default messages if not set
    if update_available and not release_notes:
        release_notes = "Bug fixes and performance improvements"

    if update_required and not force_update_message:
        force_update_message = (
            "This version is no longer supported. Please update to continue using ReelIn."
        )

    return VersionCheckResponse(
        latest_version=latest_version,
        min_version=min_version,
        current_version=current_version,
        update_required=update_required,
        update_available=update_available,
        store_url=store_url,
        release_notes=release_notes if update_available else None,
        force_update_message=force_update_message if update_required else None,
    )
