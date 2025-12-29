"""Admin settings endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import UserAccount
from app.models.app_settings import AppSettings
from app.schemas.app_settings import AppSettingsResponse, AppSettingsUpdate
from app.core.permissions import AdminOnly

router = APIRouter()


async def get_or_create_settings(db: AsyncSession) -> AppSettings:
    """Get app settings, creating default row if it doesn't exist."""
    result = await db.execute(select(AppSettings).where(AppSettings.id == 1))
    settings = result.scalar_one_or_none()

    if not settings:
        settings = AppSettings(id=1)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

    return settings


@router.get("", response_model=AppSettingsResponse)
async def get_app_settings(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> AppSettings:
    """
    Get current app settings.

    Admin only endpoint.
    """
    return await get_or_create_settings(db)


@router.patch("", response_model=AppSettingsResponse)
async def update_app_settings(
    update_data: AppSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> AppSettings:
    """
    Update app settings.

    Admin only endpoint. Only provided fields will be updated.

    Use this to:
    - Update app version when publishing to stores
    - Force updates by increasing min_version
    - Update store URLs
    - Set release notes or force update messages
    """
    settings = await get_or_create_settings(db)

    # Update only provided fields
    update_dict = update_data.model_dump(exclude_unset=True)

    if not update_dict:
        raise HTTPException(status_code=400, detail="No fields to update")

    for field, value in update_dict.items():
        setattr(settings, field, value)

    # Track who made the update
    settings.updated_by_id = current_user.id

    await db.commit()
    await db.refresh(settings)

    return settings
