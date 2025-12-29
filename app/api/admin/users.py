"""Admin user management endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.permissions import AdminOnly
from app.models.user import UserAccount, UserProfile
from app.schemas.user import UserResponse, AdminUserProfileUpdate
from app.schemas.common import PaginatedResponse, MessageResponse
from app.models.admin import AdminActionLog, AdminActionType

router = APIRouter()


@router.get("", response_model=PaginatedResponse[UserResponse])
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = None,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    List all users with pagination and filters.
    Admin only.
    """
    query = select(UserAccount).options(selectinload(UserAccount.profile))

    # Search by email or name
    if search:
        query = query.join(UserProfile).where(
            or_(
                UserAccount.email.ilike(f"%{search}%"),
                UserProfile.first_name.ilike(f"%{search}%"),
                UserProfile.last_name.ilike(f"%{search}%"),
            )
        )

    # Filter by role
    if role:
        query = query.join(UserProfile).where(
            UserProfile.roles.contains([role])
        )

    # Filter by active status
    if is_active is not None:
        query = query.where(UserAccount.is_active == is_active)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination
    query = query.order_by(UserAccount.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    users = result.scalars().all()

    return PaginatedResponse.create(
        items=users,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> UserAccount:
    """Get user by ID. Admin only."""
    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == user_id)
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user


@router.patch("/{user_id}/status", response_model=MessageResponse)
async def toggle_user_status(
    user_id: int,
    is_active: bool,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """Activate or deactivate a user. Admin only."""
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own status",
        )

    query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user.is_active = is_active
    await db.commit()

    action = "activated" if is_active else "deactivated"
    return {"message": f"User {action} successfully"}


@router.patch("/{user_id}/roles", response_model=UserResponse)
async def update_user_roles(
    user_id: int,
    roles: list[str],
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> UserAccount:
    """Update user roles. Admin only."""
    # Validate roles
    valid_roles = {"angler", "organizer", "validator", "administrator", "sponsor"}
    invalid_roles = set(roles) - valid_roles
    if invalid_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid roles: {', '.join(invalid_roles)}",
        )

    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == user_id)
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not user.profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )

    user.profile.roles = roles
    await db.commit()
    await db.refresh(user)

    return user


@router.patch("/{user_id}/profile", response_model=UserResponse)
async def update_user_profile(
    user_id: int,
    update_data: AdminUserProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> UserAccount:
    """
    Update user profile fields. Admin only.

    Can update:
    - Account: email, is_verified
    - Profile: first_name, last_name, phone, bio, gender, profile_picture_url
    - Location: country_id, city_id
    - Social: facebook_url, instagram_url, tiktok_url, youtube_url
    - Privacy: is_profile_public
    """
    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == user_id)
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not user.profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )

    # Track changes for audit log
    changes = {}

    # Update account fields
    if update_data.email is not None and update_data.email != user.email:
        # Check if email is already taken
        existing = await db.execute(
            select(UserAccount).where(
                UserAccount.email == update_data.email,
                UserAccount.id != user_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use",
            )
        changes["email"] = {"from": user.email, "to": update_data.email}
        user.email = update_data.email

    if update_data.is_verified is not None:
        changes["is_verified"] = {"from": user.is_verified, "to": update_data.is_verified}
        user.is_verified = update_data.is_verified

    # Update profile fields
    profile_fields = [
        "first_name", "last_name", "phone", "bio", "gender",
        "profile_picture_url", "country_id", "city_id",
        "facebook_url", "instagram_url", "tiktok_url", "youtube_url",
        "is_profile_public",
    ]

    for field in profile_fields:
        new_value = getattr(update_data, field, None)
        if new_value is not None:
            old_value = getattr(user.profile, field)
            if old_value != new_value:
                changes[field] = {"from": old_value, "to": new_value}
                setattr(user.profile, field, new_value)

    # Log admin action if changes were made
    if changes:
        audit_log = AdminActionLog(
            admin_id=current_user.id,
            action_type=AdminActionType.USER_PROFILE_UPDATED.value,
            target_user_id=user_id,
            details={"changes": changes},
        )
        db.add(audit_log)

    await db.commit()
    await db.refresh(user)

    return user
