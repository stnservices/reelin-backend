"""User management endpoints."""

from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status, Body
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount, UserProfile
from app.models.follow import UserFollow
from app.models.admin import AdminActionLog, AdminActionType
from app.models.notification import UserNotificationPreferences, UserDeviceToken
from app.schemas.user import (
    UserResponse,
    UserProfileUpdate,
    UserProfileResponse,
    UserListResponse,
    NotificationPreferencesResponse,
    NotificationPreferencesUpdate,
    DeviceTokenRegister,
    DeviceTokenResponse,
)
from app.schemas.common import MessageResponse
from app.core.permissions import AdminOnly, OrganizerOrAdmin
from app.core.rate_limit import limiter, USER_SEARCH_RATE_LIMIT
from app.core.security import get_password_hash
from app.services.account_deletion import account_deletion_service, AccountDeletionError

router = APIRouter()


# ============== Account Deletion Schemas ==============


class AccountDeletionRequest(BaseModel):
    """Request to delete account."""
    confirmation: str = Field(..., description="Must be 'DELETE' to confirm")
    password: Optional[str] = Field(None, description="Password for verification (required for password-based accounts)")


class AccountDeletionResponse(BaseModel):
    """Response after scheduling account deletion."""
    message: str
    deletion_scheduled_at: str
    permanent_deletion_at: str
    grace_period_days: int
    can_recover: bool


class AccountRecoveryRequest(BaseModel):
    """Request to recover account."""
    confirm_recovery: bool = Field(..., description="Must be true to confirm recovery")


class AccountRecoveryResponse(BaseModel):
    """Response after account recovery."""
    message: str
    recovered_at: str


@router.get("", response_model=UserListResponse)
async def list_users(
    search: str | None = Query(None, min_length=1),
    role: str | None = Query(None),
    is_active: bool = Query(True, description="Filter by active status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    List all users (admin only).
    Use is_active=false to list inactive users.
    """
    # Build query
    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.is_active == is_active)
    )

    # Search by email or name
    if search:
        search_term = f"%{search}%"
        query = query.join(UserProfile, isouter=True).where(
            (UserAccount.email.ilike(search_term)) |
            (UserProfile.first_name.ilike(search_term)) |
            (UserProfile.last_name.ilike(search_term))
        )

    # Filter by role
    if role:
        query = query.join(UserProfile, isouter=True).where(
            UserProfile.roles.contains([role])
        )

    # Get total count
    count_query = select(func.count(UserAccount.id)).where(UserAccount.is_active == is_active)
    if search:
        search_term = f"%{search}%"
        count_query = count_query.join(UserProfile, isouter=True).where(
            (UserAccount.email.ilike(search_term)) |
            (UserProfile.first_name.ilike(search_term)) |
            (UserProfile.last_name.ilike(search_term))
        )
    if role:
        count_query = count_query.join(UserProfile, isouter=True).where(
            UserProfile.roles.contains([role])
        )
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Pagination
    offset = (page - 1) * page_size
    query = query.order_by(UserAccount.created_at.desc()).offset(offset).limit(page_size)

    result = await db.execute(query)
    users = result.scalars().unique().all()

    return UserListResponse(
        items=[UserResponse.model_validate(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.get("/profile", response_model=UserProfileResponse)
async def get_my_profile(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user's profile."""
    if not current_user.profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )

    profile = current_user.profile

    # Get follower/following counts
    follower_result = await db.execute(
        select(func.count(UserFollow.id)).where(UserFollow.following_id == current_user.id)
    )
    follower_count = follower_result.scalar() or 0

    following_result = await db.execute(
        select(func.count(UserFollow.id)).where(UserFollow.follower_id == current_user.id)
    )
    following_count = following_result.scalar() or 0

    # Build response with location names and new fields
    return UserProfileResponse(
        id=profile.id,
        first_name=profile.first_name,
        last_name=profile.last_name,
        phone=profile.phone,
        bio=profile.bio,
        gender=profile.gender,
        profile_picture_url=profile.profile_picture_url,
        roles=profile.roles or [],
        country_id=profile.country_id,
        city_id=profile.city_id,
        country_name=profile.country.name if profile.country else None,
        city_name=profile.city.name if profile.city else None,
        facebook_url=profile.facebook_url,
        instagram_url=profile.instagram_url,
        tiktok_url=profile.tiktok_url,
        youtube_url=profile.youtube_url,
        is_profile_public=profile.is_profile_public,
        follower_count=follower_count,
        following_count=following_count,
        created_at=profile.created_at,
    )


@router.patch("/profile", response_model=UserProfileResponse)
async def update_my_profile(
    profile_data: UserProfileUpdate,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update current user's profile."""
    if not current_user.profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )

    profile = current_user.profile
    update_data = profile_data.model_dump(exclude_unset=True)

    # PRO validation: Only PRO users can set social links
    social_link_fields = ["facebook_url", "instagram_url", "tiktok_url", "youtube_url"]
    has_social_link_update = any(field in update_data for field in social_link_fields)

    if has_social_link_update:
        # Import here to avoid circular dependency
        from app.api.v1.pro import is_user_pro

        is_pro = await is_user_pro(current_user.id, db)
        if not is_pro:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Social links are a PRO feature. Upgrade to PRO to add your social media profiles.",
            )

    for field, value in update_data.items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile, ["country", "city"])

    # Get follower/following counts
    follower_result = await db.execute(
        select(func.count(UserFollow.id)).where(UserFollow.following_id == current_user.id)
    )
    follower_count = follower_result.scalar() or 0

    following_result = await db.execute(
        select(func.count(UserFollow.id)).where(UserFollow.follower_id == current_user.id)
    )
    following_count = following_result.scalar() or 0

    # Build response with location names and new fields
    return UserProfileResponse(
        id=profile.id,
        first_name=profile.first_name,
        last_name=profile.last_name,
        phone=profile.phone,
        bio=profile.bio,
        gender=profile.gender,
        profile_picture_url=profile.profile_picture_url,
        roles=profile.roles or [],
        country_id=profile.country_id,
        city_id=profile.city_id,
        country_name=profile.country.name if profile.country else None,
        city_name=profile.city.name if profile.city else None,
        facebook_url=profile.facebook_url,
        instagram_url=profile.instagram_url,
        tiktok_url=profile.tiktok_url,
        youtube_url=profile.youtube_url,
        is_profile_public=profile.is_profile_public,
        follower_count=follower_count,
        following_count=following_count,
        created_at=profile.created_at,
    )


# ============== Account Deletion Endpoints ==============
# NOTE: Must be defined BEFORE /{user_id} route to avoid path conflicts


@router.delete("/me", response_model=AccountDeletionResponse)
async def delete_my_account(
    request: AccountDeletionRequest,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Schedule account for deletion with grace period.

    User must confirm by sending confirmation='DELETE'.
    For password-based accounts, password verification is required.

    After scheduling, user has a grace period (default 30 days) to recover
    their account by logging back in.
    """
    # Validate confirmation - accept both DELETE (English) and STERGE (Romanian)
    if request.confirmation.upper() not in ("DELETE", "STERGE"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirmation_invalid"  # Generic code for frontend to show localized message
        )

    try:
        result = await account_deletion_service.schedule_deletion(
            user_id=current_user.id,
            password=request.password,
            db=db
        )

        return AccountDeletionResponse(
            message=result["message"],
            deletion_scheduled_at=result["deletion_scheduled_at"].isoformat(),
            permanent_deletion_at=result["permanent_deletion_at"].isoformat(),
            grace_period_days=result["grace_period_days"],
            can_recover=result["can_recover"]
        )

    except AccountDeletionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/me/recover", response_model=AccountRecoveryResponse)
async def recover_my_account(
    request: AccountRecoveryRequest,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel scheduled deletion and recover account.

    This endpoint is called after user logs in with an account
    that is pending deletion and chooses to recover it.
    """
    if not request.confirm_recovery:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set confirm_recovery to true to recover your account"
        )

    try:
        result = await account_deletion_service.recover_account(
            user_id=current_user.id,
            db=db
        )

        return AccountRecoveryResponse(
            message=result["message"],
            recovered_at=result["recovered_at"].isoformat()
        )

    except AccountDeletionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/me/deletion-status")
async def get_my_deletion_status(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get current account deletion status.

    Returns null if account is not pending deletion.
    """
    result = await account_deletion_service.check_pending_deletion(current_user, db)

    if not result:
        return {"pending_deletion": False}

    return {
        "pending_deletion": True,
        "deletion_scheduled_at": result["deletion_scheduled_at"].isoformat(),
        "permanent_deletion_at": result["permanent_deletion_at"].isoformat(),
        "days_remaining": result["days_remaining"],
        "can_recover": result["can_recover"]
    }


# ============== User Search Endpoint (for organizers) ==============
# NOTE: Must be defined BEFORE /{user_id} route to avoid path conflicts


class UserSearchResponse(BaseModel):
    """Response for user search by email."""

    id: int
    email: str
    display_name: str
    avatar_url: Optional[str] = None


class UserSearchMultipleResponse(BaseModel):
    """Response for multi-user search by name or email (Story 14.2)."""

    users: list[UserSearchResponse]
    total: int


@router.get("/search", response_model=UserSearchResponse)
async def search_user_by_email(
    email: str = Query(..., description="Email address to search for"),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """
    Search for a user by exact email address.
    Only organizers and admins can use this endpoint.
    Used for admin-enrolling users into events.
    """
    # Case-insensitive email search
    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(func.lower(UserAccount.email) == email.lower())
        .where(UserAccount.is_active == True)  # noqa: E712
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with email '{email}' not found"
        )

    # Build display name
    display_name = user.email
    if user.profile:
        if user.profile.first_name or user.profile.last_name:
            display_name = f"{user.profile.first_name or ''} {user.profile.last_name or ''}".strip()

    return UserSearchResponse(
        id=user.id,
        email=user.email,
        display_name=display_name,
        avatar_url=user.profile.profile_picture_url if user.profile else None,
    )


@router.get("/search-multiple", response_model=UserSearchMultipleResponse)
@limiter.limit(USER_SEARCH_RATE_LIMIT)
async def search_users_multiple(
    request: Request,
    q: str = Query(..., min_length=2, description="Search query (name or email, min 2 characters)"),
    limit: int = Query(20, le=50, description="Maximum results to return"),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """
    Search users by name or email (partial match).
    Only organizers and admins can use this endpoint.
    Used for admin-enrolling users into events (Story 14.2).

    - Searches across email AND display_name fields (case-insensitive)
    - Supports partial matching (ILIKE)
    - Returns list of matching users (max 20 by default)
    - Returns empty array if no matches (not 404)
    """
    from sqlalchemy import or_

    search_term = q.lower()

    # Build query with ILIKE partial matching on email and profile names
    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.is_active == True)  # noqa: E712
        .where(
            or_(
                func.lower(UserAccount.email).contains(search_term),
                func.lower(UserProfile.first_name).contains(search_term),
                func.lower(UserProfile.last_name).contains(search_term),
            )
        )
        .outerjoin(UserProfile, UserAccount.id == UserProfile.user_id)
        .limit(limit)
    )

    result = await db.execute(query)
    users = result.scalars().unique().all()

    # Build response
    user_responses = []
    for user in users:
        display_name = user.email
        if user.profile:
            if user.profile.first_name or user.profile.last_name:
                display_name = f"{user.profile.first_name or ''} {user.profile.last_name or ''}".strip()

        user_responses.append(
            UserSearchResponse(
                id=user.id,
                email=user.email,
                display_name=display_name,
                avatar_url=user.profile.profile_picture_url if user.profile else None,
            )
        )

    return UserSearchMultipleResponse(
        users=user_responses,
        total=len(user_responses),
    )


# ============== Notification Preferences Endpoints ==============
# NOTE: These must be defined BEFORE /{user_id} route to avoid path conflicts


@router.get("/notification-preferences", response_model=NotificationPreferencesResponse)
async def get_notification_preferences(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get current user's notification preferences.
    Creates default preferences if none exist.
    """
    query = select(UserNotificationPreferences).where(
        UserNotificationPreferences.user_id == current_user.id
    )
    result = await db.execute(query)
    prefs = result.scalar_one_or_none()

    if not prefs:
        # Create default preferences
        prefs = UserNotificationPreferences(
            user_id=current_user.id,
            notify_events_from_country=True,
            notify_event_types=[],
            notify_from_clubs=[],
            notify_event_catches="all",
        )
        db.add(prefs)
        await db.commit()
        await db.refresh(prefs)

    return prefs


@router.patch("/notification-preferences", response_model=NotificationPreferencesResponse)
async def update_notification_preferences(
    prefs_data: NotificationPreferencesUpdate,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update current user's notification preferences."""
    query = select(UserNotificationPreferences).where(
        UserNotificationPreferences.user_id == current_user.id
    )
    result = await db.execute(query)
    prefs = result.scalar_one_or_none()

    if not prefs:
        # Create new preferences with provided data
        prefs = UserNotificationPreferences(
            user_id=current_user.id,
            notify_events_from_country=prefs_data.notify_events_from_country if prefs_data.notify_events_from_country is not None else True,
            notify_event_types=prefs_data.notify_event_types if prefs_data.notify_event_types is not None else [],
            notify_from_clubs=prefs_data.notify_from_clubs if prefs_data.notify_from_clubs is not None else [],
            notify_event_catches=prefs_data.notify_event_catches if prefs_data.notify_event_catches is not None else "all",
        )
        db.add(prefs)
    else:
        # Update existing preferences
        update_data = prefs_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if value is not None:
                setattr(prefs, field, value)

    await db.commit()
    await db.refresh(prefs)

    return prefs


# ============== Device Token Endpoints ==============


@router.post("/devices/register", response_model=DeviceTokenResponse, status_code=201)
async def register_device_token(
    token_data: DeviceTokenRegister,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Register a device token for push notifications.
    If the token already exists, updates it to the current user.
    """
    # Check if token already exists
    query = select(UserDeviceToken).where(UserDeviceToken.token == token_data.token)
    result = await db.execute(query)
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing token to current user
        existing.user_id = current_user.id
        existing.device_type = token_data.device_type
        await db.commit()
        await db.refresh(existing)
        return existing

    # Create new token
    device_token = UserDeviceToken(
        user_id=current_user.id,
        token=token_data.token,
        device_type=token_data.device_type,
    )
    db.add(device_token)
    await db.commit()
    await db.refresh(device_token)

    return device_token


@router.delete("/devices/{token}", response_model=MessageResponse)
async def unregister_device_token(
    token: str,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Unregister a device token for push notifications."""
    query = select(UserDeviceToken).where(
        UserDeviceToken.token == token,
        UserDeviceToken.user_id == current_user.id,
    )
    result = await db.execute(query)
    device_token = result.scalar_one_or_none()

    if not device_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device token not found",
        )

    await db.delete(device_token)
    await db.commit()

    return {"message": "Device token unregistered successfully"}


# ============== User Management Endpoints ==============


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserAccount:
    """Get user by ID (requires authentication)."""
    query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user


# ============== Admin User Management Endpoints ==============


class UserStatusUpdate(BaseModel):
    """Schema for updating user status."""
    is_active: bool


class UserRolesUpdate(BaseModel):
    """Schema for updating user roles."""
    roles: list[str] = Field(..., min_length=1)


class PasswordResetRequest(BaseModel):
    """Schema for admin password reset."""
    new_password: str = Field(..., min_length=8)


@router.get("/validators/list", response_model=UserListResponse)
async def list_validators(
    search: str | None = Query(None, min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """
    List users with validator role.
    Useful for organizers when assigning validators to events.
    """
    # Build query for users with validator role
    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .join(UserProfile)
        .where(
            UserAccount.is_active == True,
            UserProfile.roles.contains(["validator"])
        )
    )

    # Search by email or name
    if search:
        search_term = f"%{search}%"
        query = query.where(
            (UserAccount.email.ilike(search_term)) |
            (UserProfile.first_name.ilike(search_term)) |
            (UserProfile.last_name.ilike(search_term))
        )

    # Get total count
    count_subquery = query.subquery()
    count_query = select(func.count()).select_from(count_subquery)
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Pagination
    offset = (page - 1) * page_size
    query = query.order_by(UserProfile.first_name, UserProfile.last_name).offset(offset).limit(page_size)

    result = await db.execute(query)
    users = result.scalars().unique().all()

    return UserListResponse(
        items=[UserResponse.model_validate(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.patch("/{user_id}/status", response_model=UserResponse)
async def update_user_status(
    user_id: int,
    status_data: UserStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> UserAccount:
    """
    Activate or deactivate a user account.
    Admin only.
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own account status"
        )

    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == user_id)
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    old_status = user.is_active
    user.is_active = status_data.is_active

    # Log action
    log = AdminActionLog(
        admin_id=current_user.id,
        action_type=AdminActionType.USER_ACTIVATED.value if status_data.is_active else AdminActionType.USER_DEACTIVATED.value,
        target_user_id=user_id,
        details={"old_status": old_status, "new_status": status_data.is_active, "user_email": user.email}
    )
    db.add(log)

    await db.commit()
    await db.refresh(user, ["profile"])

    return user


@router.patch("/{user_id}/roles", response_model=UserResponse)
async def update_user_roles(
    user_id: int,
    roles_data: UserRolesUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> UserAccount:
    """
    Update user roles.
    Admin only.
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own roles"
        )

    # Validate roles
    valid_roles = {"angler", "organizer", "validator", "administrator", "sponsor"}
    if not set(roles_data.roles).issubset(valid_roles):
        invalid = set(roles_data.roles) - valid_roles
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid roles: {', '.join(invalid)}. Valid roles are: {', '.join(valid_roles)}"
        )

    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == user_id)
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not user.profile:
        raise HTTPException(status_code=404, detail="User profile not found")

    old_roles = list(user.profile.roles or [])
    user.profile.roles = roles_data.roles

    # Log action
    log = AdminActionLog(
        admin_id=current_user.id,
        action_type=AdminActionType.USER_ROLE_CHANGED.value,
        target_user_id=user_id,
        details={"old_roles": old_roles, "new_roles": roles_data.roles, "user_email": user.email}
    )
    db.add(log)

    await db.commit()
    await db.refresh(user, ["profile"])

    return user


@router.post("/{user_id}/reset-password", response_model=MessageResponse)
async def admin_reset_password(
    user_id: int,
    password_data: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    Reset a user's password.
    Admin only.
    """
    query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = get_password_hash(password_data.new_password)

    # Log action (without storing password)
    log = AdminActionLog(
        admin_id=current_user.id,
        action_type=AdminActionType.USER_PASSWORD_RESET.value,
        target_user_id=user_id,
        details={"reset_by": current_user.email, "user_email": user.email}
    )
    db.add(log)

    await db.commit()

    return {"message": f"Password reset successfully for user {user.email}"}


class AdminUserCreate(BaseModel):
    """Schema for admin creating a user."""
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=8)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    roles: list[str] = Field(default=["angler"])
    is_verified: bool = Field(default=True)


@router.post("", response_model=UserResponse, status_code=201)
async def admin_create_user(
    user_data: AdminUserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> UserAccount:
    """
    Create a new user (admin only).
    Useful for creating internal users like validators or organizers.
    """
    # Check if email already exists
    existing_query = select(UserAccount).where(UserAccount.email == user_data.email)
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Validate roles
    valid_roles = {"angler", "organizer", "validator", "administrator", "sponsor"}
    if not set(user_data.roles).issubset(valid_roles):
        invalid = set(user_data.roles) - valid_roles
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid roles: {', '.join(invalid)}. Valid roles are: {', '.join(valid_roles)}"
        )

    # Create user account
    user = UserAccount(
        email=user_data.email,
        password_hash=get_password_hash(user_data.password),
        is_active=True,
        is_verified=user_data.is_verified,
    )
    db.add(user)
    await db.flush()

    # Create user profile
    profile = UserProfile(
        user_id=user.id,
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        roles=user_data.roles,
    )
    db.add(profile)

    # Log action
    log = AdminActionLog(
        admin_id=current_user.id,
        action_type=AdminActionType.USER_ACTIVATED.value,  # Using activated as "created"
        target_user_id=user.id,
        details={
            "created_by": current_user.email,
            "user_email": user_data.email,
            "roles": user_data.roles,
            "action": "user_created"
        }
    )
    db.add(log)

    await db.commit()
    await db.refresh(user, ["profile"])

    return user
