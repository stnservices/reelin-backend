"""User waypoints API endpoints.

Provides CRUD operations for user's private waypoints (fishing spots).
Pro users get unlimited waypoints, free users are limited to 3.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from math import ceil
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models import UserAccount, UserWaypoint, WaypointIcon, WaypointCategory, UserFollow
from app.models.pro import ProSettings
from app.schemas.waypoint import (
    WaypointCreate,
    WaypointUpdate,
    WaypointResponse,
    WaypointListResponse,
    WaypointShareRequest,
    WaypointShareResponse,
    SharedWaypointUser,
    SharedWaypointResponse,
    WaypointConfigResponse,
    WaypointIconResponse,
    WaypointCategoryResponse,
)
from app.api.v1.pro import is_user_pro

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/waypoints", tags=["waypoints"])


# ============== Constants ==============

DEFAULT_FREE_LIMIT = 3
DEFAULT_DESCRIPTION_MAX_CHARS = 500


# ============== Helper Functions ==============


async def get_waypoint_free_limit(db: AsyncSession) -> int:
    """Get the waypoint limit for free users from ProSettings."""
    result = await db.execute(
        select(ProSettings).where(ProSettings.key == "waypoint_free_limit")
    )
    setting = result.scalar_one_or_none()
    if setting and setting.value:
        try:
            return int(setting.value)
        except ValueError:
            pass
    return DEFAULT_FREE_LIMIT


async def get_user_waypoint_count(db: AsyncSession, user_id: int) -> int:
    """Get the number of waypoints owned by a user."""
    result = await db.execute(
        select(func.count(UserWaypoint.id)).where(UserWaypoint.user_id == user_id)
    )
    return result.scalar() or 0


async def can_user_add_waypoint(db: AsyncSession, user_id: int) -> tuple[bool, int, int]:
    """
    Check if user can add more waypoints.

    Returns: (can_add, current_count, limit)
    """
    is_pro = await is_user_pro(user_id, db)
    if is_pro:
        current = await get_user_waypoint_count(db, user_id)
        return True, current, -1  # -1 means unlimited

    limit = await get_waypoint_free_limit(db)
    current = await get_user_waypoint_count(db, user_id)
    return current < limit, current, limit


def waypoint_to_response(waypoint: UserWaypoint) -> WaypointResponse:
    """Convert a waypoint model to response schema."""
    shared_with = waypoint.shared_with or []
    return WaypointResponse(
        id=waypoint.id,
        latitude=float(waypoint.latitude),
        longitude=float(waypoint.longitude),
        name=waypoint.name,
        description=waypoint.description,
        icon=waypoint.icon,
        color=waypoint.color,
        category=waypoint.category,
        photo_url=waypoint.photo_url,
        is_shared=waypoint.is_shared,
        shared_with_count=len(shared_with),
        created_at=waypoint.created_at,
        updated_at=waypoint.updated_at,
    )


# ============== Endpoints ==============


@router.get("/config", response_model=WaypointConfigResponse)
async def get_waypoint_config(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get waypoint configuration (icons, categories, limits).

    Use this to populate UI pickers and show limit info.
    """
    # Get icons
    icons_result = await db.execute(
        select(WaypointIcon)
        .where(WaypointIcon.is_active == True)
        .order_by(WaypointIcon.display_order)
    )
    icons = icons_result.scalars().all()

    # Get categories
    categories_result = await db.execute(
        select(WaypointCategory)
        .where(WaypointCategory.is_active == True)
        .order_by(WaypointCategory.display_order)
    )
    categories = categories_result.scalars().all()

    # Get Pro status and limits
    is_pro = await is_user_pro(current_user.id, db)
    free_limit = await get_waypoint_free_limit(db)
    current_count = await get_user_waypoint_count(db, current_user.id)

    # Filter Pro-only icons if not Pro
    filtered_icons = [
        WaypointIconResponse(
            id=icon.id,
            code=icon.code,
            name=icon.name,
            emoji=icon.emoji,
            svg_url=icon.svg_url,
            is_pro_only=icon.is_pro_only,
        )
        for icon in icons
        if is_pro or not icon.is_pro_only
    ]

    return WaypointConfigResponse(
        icons=filtered_icons,
        categories=[
            WaypointCategoryResponse(
                id=cat.id,
                code=cat.code,
                name=cat.name,
                color=cat.color,
            )
            for cat in categories
        ],
        free_limit=free_limit,
        is_pro=is_pro,
        current_count=current_count,
        can_add_more=is_pro or current_count < free_limit,
    )


@router.get("", response_model=WaypointListResponse)
async def list_waypoints(
    category: Optional[str] = Query(None, description="Filter by category"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List user's waypoints with optional category filter.
    """
    # Base query
    query = select(UserWaypoint).where(UserWaypoint.user_id == current_user.id)

    # Apply category filter
    if category:
        query = query.where(UserWaypoint.category == category)

    # Get total count
    count_query = select(func.count(UserWaypoint.id)).where(
        UserWaypoint.user_id == current_user.id
    )
    if category:
        count_query = count_query.where(UserWaypoint.category == category)
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Get paginated results
    offset = (page - 1) * page_size
    query = query.order_by(UserWaypoint.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    waypoints = result.scalars().all()

    return WaypointListResponse(
        items=[waypoint_to_response(wp) for wp in waypoints],
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.post("", response_model=WaypointResponse, status_code=status.HTTP_201_CREATED)
async def create_waypoint(
    waypoint_data: WaypointCreate,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new waypoint.

    Free users are limited to 3 waypoints.
    Description is Pro-only (will be ignored for free users).
    """
    # Check if user can add more waypoints
    can_add, current, limit = await can_user_add_waypoint(db, current_user.id)
    if not can_add:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Free users are limited to {limit} waypoints. Upgrade to Pro for unlimited.",
        )

    # Check for duplicate location
    existing = await db.execute(
        select(UserWaypoint).where(
            UserWaypoint.user_id == current_user.id,
            UserWaypoint.latitude == Decimal(str(waypoint_data.latitude)),
            UserWaypoint.longitude == Decimal(str(waypoint_data.longitude)),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You already have a waypoint at this location.",
        )

    # Pro features check
    is_pro = await is_user_pro(current_user.id, db)
    description = waypoint_data.description if is_pro else None

    # Create waypoint
    waypoint = UserWaypoint(
        user_id=current_user.id,
        latitude=Decimal(str(waypoint_data.latitude)),
        longitude=Decimal(str(waypoint_data.longitude)),
        name=waypoint_data.name,
        description=description,
        icon=waypoint_data.icon,
        color=waypoint_data.color,
        category=waypoint_data.category,
    )
    db.add(waypoint)
    await db.commit()
    await db.refresh(waypoint)

    logger.info(f"User {current_user.id} created waypoint {waypoint.id}")

    return waypoint_to_response(waypoint)


@router.get("/shared-with-me", response_model=List[SharedWaypointResponse])
async def get_shared_with_me(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get waypoints that others have shared with you.
    """
    # Find waypoints where current user is in shared_with array
    result = await db.execute(
        select(UserWaypoint)
        .options(selectinload(UserWaypoint.user).selectinload(UserAccount.profile))
        .where(UserWaypoint.shared_with.contains([current_user.id]))
        .order_by(UserWaypoint.created_at.desc())
    )
    waypoints = result.scalars().all()

    shared_waypoints = []
    for wp in waypoints:
        owner_name = wp.user.email
        owner_avatar = None
        if wp.user.profile:
            owner_name = wp.user.profile.full_name or wp.user.email
            owner_avatar = wp.user.profile.profile_picture_url

        shared_waypoints.append(
            SharedWaypointResponse(
                id=wp.id,
                latitude=float(wp.latitude),
                longitude=float(wp.longitude),
                name=wp.name,
                description=wp.description,
                icon=wp.icon,
                color=wp.color,
                category=wp.category,
                photo_url=wp.photo_url,
                owner_id=wp.user_id,
                owner_name=owner_name,
                owner_avatar_url=owner_avatar,
                created_at=wp.created_at,
            )
        )

    return shared_waypoints


@router.get("/{waypoint_id}", response_model=WaypointResponse)
async def get_waypoint(
    waypoint_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a single waypoint by ID.

    Must be the owner or have the waypoint shared with you.
    """
    result = await db.execute(
        select(UserWaypoint).where(UserWaypoint.id == waypoint_id)
    )
    waypoint = result.scalar_one_or_none()

    if not waypoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waypoint not found",
        )

    # Check access: owner or shared with
    shared_with = waypoint.shared_with or []
    if waypoint.user_id != current_user.id and current_user.id not in shared_with:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waypoint not found",
        )

    return waypoint_to_response(waypoint)


@router.patch("/{waypoint_id}", response_model=WaypointResponse)
async def update_waypoint(
    waypoint_id: int,
    update_data: WaypointUpdate,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update a waypoint.

    Only the owner can update.
    Description is Pro-only.
    """
    result = await db.execute(
        select(UserWaypoint).where(
            UserWaypoint.id == waypoint_id,
            UserWaypoint.user_id == current_user.id,
        )
    )
    waypoint = result.scalar_one_or_none()

    if not waypoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waypoint not found",
        )

    # Pro features check
    is_pro = await is_user_pro(current_user.id, db)

    # Update fields
    update_dict = update_data.model_dump(exclude_unset=True)

    # Strip description for free users
    if "description" in update_dict and not is_pro:
        del update_dict["description"]

    for field, value in update_dict.items():
        setattr(waypoint, field, value)

    waypoint.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(waypoint)

    return waypoint_to_response(waypoint)


@router.delete("/{waypoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_waypoint(
    waypoint_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a waypoint.

    Only the owner can delete.
    """
    result = await db.execute(
        select(UserWaypoint).where(
            UserWaypoint.id == waypoint_id,
            UserWaypoint.user_id == current_user.id,
        )
    )
    waypoint = result.scalar_one_or_none()

    if not waypoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waypoint not found",
        )

    await db.delete(waypoint)
    await db.commit()

    logger.info(f"User {current_user.id} deleted waypoint {waypoint_id}")


# ============== Sharing Endpoints (Pro Only) ==============


@router.get("/{waypoint_id}/share/suggestions", response_model=List[SharedWaypointUser])
async def get_share_suggestions(
    waypoint_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get share suggestions from following list.

    Pro users only.
    """
    # Check Pro status
    is_pro = await is_user_pro(current_user.id, db)
    if not is_pro:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sharing is a Pro feature",
        )

    # Get waypoint
    result = await db.execute(
        select(UserWaypoint).where(
            UserWaypoint.id == waypoint_id,
            UserWaypoint.user_id == current_user.id,
        )
    )
    waypoint = result.scalar_one_or_none()

    if not waypoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waypoint not found",
        )

    # Get following list
    following_result = await db.execute(
        select(UserFollow)
        .options(selectinload(UserFollow.following).selectinload(UserAccount.profile))
        .where(UserFollow.follower_id == current_user.id)
    )
    follows = following_result.scalars().all()

    # Get already shared IDs
    already_shared = set(waypoint.shared_with or [])

    suggestions = []
    for follow in follows:
        user = follow.following
        name = user.email
        avatar_url = None
        if user.profile:
            name = user.profile.full_name or user.email
            avatar_url = user.profile.profile_picture_url

        suggestions.append(
            SharedWaypointUser(
                id=user.id,
                name=name,
                avatar_url=avatar_url,
                already_shared=user.id in already_shared,
            )
        )

    return suggestions


@router.post("/{waypoint_id}/share", response_model=WaypointShareResponse)
async def share_waypoint(
    waypoint_id: int,
    share_request: WaypointShareRequest,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Share a waypoint with specific users.

    Pro users only. Users must be in your following list.
    """
    # Check Pro status
    is_pro = await is_user_pro(current_user.id, db)
    if not is_pro:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sharing is a Pro feature",
        )

    # Get waypoint
    result = await db.execute(
        select(UserWaypoint).where(
            UserWaypoint.id == waypoint_id,
            UserWaypoint.user_id == current_user.id,
        )
    )
    waypoint = result.scalar_one_or_none()

    if not waypoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waypoint not found",
        )

    # Validate that all user_ids are in following list
    following_result = await db.execute(
        select(UserFollow.following_id).where(
            UserFollow.follower_id == current_user.id
        )
    )
    following_ids = {row[0] for row in following_result.fetchall()}

    invalid_ids = set(share_request.user_ids) - following_ids
    if invalid_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You can only share with users you follow. Invalid IDs: {list(invalid_ids)}",
        )

    # Update shared_with list
    current_shared = set(waypoint.shared_with or [])
    current_shared.update(share_request.user_ids)
    waypoint.shared_with = list(current_shared)
    waypoint.is_shared = len(current_shared) > 0
    waypoint.updated_at = datetime.now(timezone.utc)

    await db.commit()

    # Get user details for response
    users_result = await db.execute(
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id.in_(list(current_shared)))
    )
    users = users_result.scalars().all()

    shared_users = []
    for user in users:
        name = user.email
        avatar_url = None
        if user.profile:
            name = user.profile.full_name or user.email
            avatar_url = user.profile.profile_picture_url

        shared_users.append(
            SharedWaypointUser(
                id=user.id,
                name=name,
                avatar_url=avatar_url,
                already_shared=True,
            )
        )

    logger.info(
        f"User {current_user.id} shared waypoint {waypoint_id} with {len(share_request.user_ids)} users"
    )

    return WaypointShareResponse(
        shared_with=shared_users,
        total_shared=len(current_shared),
    )


@router.delete("/{waypoint_id}/share", response_model=WaypointShareResponse)
async def unshare_waypoint(
    waypoint_id: int,
    user_ids: List[int] = Query(..., description="User IDs to remove from sharing"),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Remove users from a waypoint's share list.

    Pro users only.
    """
    # Check Pro status
    is_pro = await is_user_pro(current_user.id, db)
    if not is_pro:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sharing is a Pro feature",
        )

    # Get waypoint
    result = await db.execute(
        select(UserWaypoint).where(
            UserWaypoint.id == waypoint_id,
            UserWaypoint.user_id == current_user.id,
        )
    )
    waypoint = result.scalar_one_or_none()

    if not waypoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waypoint not found",
        )

    # Update shared_with list
    current_shared = set(waypoint.shared_with or [])
    current_shared -= set(user_ids)
    waypoint.shared_with = list(current_shared)
    waypoint.is_shared = len(current_shared) > 0
    waypoint.updated_at = datetime.now(timezone.utc)

    await db.commit()

    # Get user details for response
    shared_users = []
    if current_shared:
        users_result = await db.execute(
            select(UserAccount)
            .options(selectinload(UserAccount.profile))
            .where(UserAccount.id.in_(list(current_shared)))
        )
        users = users_result.scalars().all()

        for user in users:
            name = user.email
            avatar_url = None
            if user.profile:
                name = user.profile.full_name or user.email
                avatar_url = user.profile.profile_picture_url

            shared_users.append(
                SharedWaypointUser(
                    id=user.id,
                    name=name,
                    avatar_url=avatar_url,
                    already_shared=True,
                )
            )

    return WaypointShareResponse(
        shared_with=shared_users,
        total_shared=len(current_shared),
    )
