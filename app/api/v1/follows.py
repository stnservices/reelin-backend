"""User follow system and Angler Card endpoints."""

import logging
from datetime import datetime, timedelta
from math import ceil
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models import UserAccount, UserProfile, UserFollow
from app.models.notification import UserDeviceToken, Notification
from app.models.statistics import UserEventTypeStats
from app.models.achievement import UserAchievement, AchievementDefinition
from app.services.push_notifications import send_push_notification
from app.api.v1.pro import is_user_pro

# Throttle follow notifications - only send once per follower per 24 hours
FOLLOW_NOTIFICATION_COOLDOWN_HOURS = 24

logger = logging.getLogger(__name__)

router = APIRouter()


# ============== Response Schemas ==============


class FollowUserResponse(BaseModel):
    """Response when following/unfollowing a user."""

    id: int
    name: str
    avatar_url: Optional[str] = None
    is_following: bool
    follower_count: int


class FollowerListItem(BaseModel):
    """User item in followers/following list."""

    id: int
    name: str
    avatar_url: Optional[str] = None
    location: Optional[str] = None
    is_following: bool  # Whether current user follows this user
    follower_count: int


class FollowerListResponse(BaseModel):
    """Paginated list of followers/following."""

    items: List[FollowerListItem]
    total: int
    page: int
    page_size: int
    pages: int


class FollowStatsResponse(BaseModel):
    """Follow statistics for a user."""

    follower_count: int
    following_count: int
    is_following: bool  # Whether current user follows this user


# ============== Angler Card Schemas ==============


class AnglerCardBadge(BaseModel):
    """Badge info for angler card."""

    id: int
    code: str
    name: str
    tier: Optional[str] = None
    icon_url: Optional[str] = None
    badge_color: Optional[str] = None


class AnglerCardStats(BaseModel):
    """Stats for angler card (the Power 4)."""

    wins: int = 0
    podiums: int = 0
    catches: int = 0
    largest_catch_cm: Optional[float] = None
    largest_catch_species: Optional[str] = None


class AnglerCardSocialLinks(BaseModel):
    """Social links for angler card (PRO only)."""

    facebook_url: Optional[str] = None
    instagram_url: Optional[str] = None
    tiktok_url: Optional[str] = None
    youtube_url: Optional[str] = None


class AnglerCardResponse(BaseModel):
    """
    Angler Card - quick-view profile data.

    Shows minimal data if profile is private.
    Social links only shown if user is PRO and profile is public.
    """

    # Always visible
    id: int
    name: str
    avatar_url: Optional[str] = None
    is_pro: bool = False
    follower_count: int = 0
    following_count: int = 0
    is_following: bool = False  # Whether current user follows this user

    # Visible only if profile is public
    location: Optional[str] = None
    stats: Optional[AnglerCardStats] = None
    badges: List[AnglerCardBadge] = []

    # Only for PRO users with public profile
    social_links: Optional[AnglerCardSocialLinks] = None

    # Privacy flag
    is_profile_public: bool = True


# ============== Helper Functions ==============


async def get_follower_count(db: AsyncSession, user_id: int) -> int:
    """Get the number of followers for a user."""
    result = await db.execute(
        select(func.count(UserFollow.id)).where(UserFollow.following_id == user_id)
    )
    return result.scalar() or 0


async def get_following_count(db: AsyncSession, user_id: int) -> int:
    """Get the number of users a user is following."""
    result = await db.execute(
        select(func.count(UserFollow.id)).where(UserFollow.follower_id == user_id)
    )
    return result.scalar() or 0


async def is_following(db: AsyncSession, follower_id: int, following_id: int) -> bool:
    """Check if follower_id is following following_id."""
    result = await db.execute(
        select(UserFollow.id).where(
            UserFollow.follower_id == follower_id,
            UserFollow.following_id == following_id,
        )
    )
    return result.scalar() is not None


async def send_follow_notification(
    db: AsyncSession,
    follower: UserAccount,
    followed_user_id: int,
) -> None:
    """
    Send push notification to the followed user.

    Rate-limited: Only sends one notification per follower per 24 hours
    to prevent follow/unfollow spam abuse.
    """
    try:
        # Check if we recently sent a follow notification from this follower
        cooldown_threshold = datetime.utcnow() - timedelta(hours=FOLLOW_NOTIFICATION_COOLDOWN_HOURS)

        recent_notification = await db.execute(
            select(Notification.id).where(
                Notification.user_id == followed_user_id,
                Notification.type == "new_follower",
                Notification.data["follower_id"].astext == str(follower.id),
                Notification.created_at >= cooldown_threshold,
            ).limit(1)
        )

        if recent_notification.scalar_one_or_none():
            logger.debug(
                f"Skipping follow notification from {follower.id} to {followed_user_id} - "
                f"already sent within {FOLLOW_NOTIFICATION_COOLDOWN_HOURS} hours"
            )
            return

        # Get device tokens for followed user
        tokens_result = await db.execute(
            select(UserDeviceToken.token).where(
                UserDeviceToken.user_id == followed_user_id
            )
        )
        tokens = [row[0] for row in tokens_result.fetchall()]

        if not tokens:
            logger.debug(f"No device tokens for user {followed_user_id}")
            # Still create in-app notification even without push tokens

        # Get follower name
        follower_name = "Someone"
        if follower.profile:
            follower_name = follower.profile.full_name or follower.email

        # Create in-app notification (for rate limit tracking)
        notification = Notification(
            user_id=followed_user_id,
            type="new_follower",
            title="New follower",
            message=f"{follower_name} is now following you",
            data={
                "follower_id": str(follower.id),
                "follower_name": follower_name,
            },
        )
        db.add(notification)
        await db.commit()

        # Send push notification if tokens exist
        if tokens:
            send_push_notification(
                tokens=tokens,
                title="New follower",
                body=f"{follower_name} is now following you",
                data={
                    "type": "new_follower",
                    "follower_id": str(follower.id),
                    "deep_link": f"/profile/{follower.id}",
                },
            )

        logger.info(f"Sent follow notification to user {followed_user_id}")

    except Exception as e:
        logger.error(f"Failed to send follow notification: {e}")


async def get_user_stats(db: AsyncSession, user_id: int) -> AnglerCardStats:
    """Get user's Power 4 stats for angler card."""
    # Get overall stats (event_type_id is NULL)
    result = await db.execute(
        select(UserEventTypeStats)
        .options(selectinload(UserEventTypeStats.largest_catch_species))
        .where(
            UserEventTypeStats.user_id == user_id,
            UserEventTypeStats.event_type_id.is_(None),
        )
    )
    stats = result.scalar_one_or_none()

    if not stats:
        return AnglerCardStats()

    return AnglerCardStats(
        wins=stats.total_wins or 0,
        podiums=stats.podium_finishes or 0,
        catches=stats.total_approved_catches or 0,
        largest_catch_cm=stats.largest_catch_cm,
        largest_catch_species=(
            stats.largest_catch_species.name if stats.largest_catch_species else None
        ),
    )


async def get_top_badges(db: AsyncSession, user_id: int, limit: int | None = None) -> List[AnglerCardBadge]:
    """Get user's earned badges for angler card."""
    # Get achievements ordered by most recent first
    query = (
        select(UserAchievement)
        .options(selectinload(UserAchievement.achievement))
        .where(UserAchievement.user_id == user_id)
        .order_by(UserAchievement.earned_at.desc())
    )
    if limit:
        query = query.limit(limit)
    result = await db.execute(query)
    achievements = result.scalars().all()

    badges = []
    for ua in achievements:
        if ua.achievement:
            badges.append(
                AnglerCardBadge(
                    id=ua.achievement.id,
                    code=ua.achievement.code,
                    name=ua.achievement.name,
                    tier=ua.achievement.tier,
                    icon_url=ua.achievement.icon_url,
                    badge_color=ua.achievement.badge_color,
                )
            )

    return badges


# ============== Endpoints ==============

# NOTE: /me/following must be defined BEFORE /{user_id} routes to avoid conflicts


@router.get("/me/following", response_model=FollowerListResponse)
async def get_my_following(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get list of users that the current user is following.
    """
    # Get total count
    count_result = await db.execute(
        select(func.count(UserFollow.id)).where(UserFollow.follower_id == current_user.id)
    )
    total = count_result.scalar() or 0

    # Get paginated following
    offset = (page - 1) * page_size
    following_result = await db.execute(
        select(UserFollow)
        .options(selectinload(UserFollow.following).selectinload(UserAccount.profile))
        .where(UserFollow.follower_id == current_user.id)
        .order_by(UserFollow.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    follows = following_result.scalars().all()

    # Build response items
    items = []
    for follow in follows:
        following = follow.following
        profile = following.profile

        name = following.email
        avatar_url = None
        location = None

        if profile:
            name = profile.full_name or following.email
            avatar_url = profile.profile_picture_url
            if profile.city:
                location = profile.city.name
            elif profile.country:
                location = profile.country.name

        # Get follower count for this user
        fc = await get_follower_count(db, following.id)

        items.append(
            FollowerListItem(
                id=following.id,
                name=name,
                avatar_url=avatar_url,
                location=location,
                is_following=True,  # We're iterating through users we follow
                follower_count=fc,
            )
        )

    return FollowerListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.get("/{user_id}/card", response_model=AnglerCardResponse)
async def get_angler_card(
    user_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get angler card data for a user.

    Returns quick-view profile data including:
    - Basic info: name, avatar, location, is_pro
    - Stats: wins, podiums, catches, largest catch (the Power 4)
    - Badges: all earned badges
    - Social links: only if user is PRO and profile is public
    - Follow info: follower count, is_following

    Private profiles return only: name, avatar, is_pro, follower count
    """
    # Get user with profile
    result = await db.execute(
        select(UserAccount)
        .options(
            selectinload(UserAccount.profile).selectinload(UserProfile.country),
            selectinload(UserAccount.profile).selectinload(UserProfile.city),
        )
        .where(UserAccount.id == user_id, UserAccount.is_active == True)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    profile = user.profile

    # Get basic info (always visible)
    name = user.email
    avatar_url = None
    if profile:
        name = profile.full_name or user.email
        avatar_url = profile.profile_picture_url

    # Check if user is PRO
    user_is_pro = await is_user_pro(user_id, db)

    # Get follower/following counts (always visible)
    follower_count = await get_follower_count(db, user_id)
    following_count = await get_following_count(db, user_id)

    # Check if current user is following this user
    is_following_user = await is_following(db, current_user.id, user_id)

    # Check privacy setting
    is_public = profile.is_profile_public if profile else True

    # Build response
    response = AnglerCardResponse(
        id=user_id,
        name=name,
        avatar_url=avatar_url,
        is_pro=user_is_pro,
        follower_count=follower_count,
        following_count=following_count,
        is_following=is_following_user,
        is_profile_public=is_public,
    )

    # Add additional data only if profile is public
    if is_public:
        # Location
        if profile:
            if profile.city:
                response.location = profile.city.name
            elif profile.country:
                response.location = profile.country.name

        # Stats (the Power 4)
        response.stats = await get_user_stats(db, user_id)

        # All earned badges
        response.badges = await get_top_badges(db, user_id)

        # Social links - only for PRO users with public profile
        if user_is_pro and profile:
            response.social_links = AnglerCardSocialLinks(
                facebook_url=profile.facebook_url,
                instagram_url=profile.instagram_url,
                tiktok_url=profile.tiktok_url,
                youtube_url=profile.youtube_url,
            )

    return response


@router.post("/{user_id}/follow", response_model=FollowUserResponse)
async def follow_user(
    user_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Follow a user.

    Returns 400 if:
    - Trying to follow yourself
    - Already following this user
    """
    # Cannot follow yourself
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot follow yourself",
        )

    # Check if user exists
    target_result = await db.execute(
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == user_id, UserAccount.is_active == True)
    )
    target_user = target_result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Check if already following
    existing = await is_following(db, current_user.id, user_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are already following this user",
        )

    # Create follow relationship
    follow = UserFollow(
        follower_id=current_user.id,
        following_id=user_id,
    )
    db.add(follow)
    await db.commit()

    # Get updated follower count
    follower_count = await get_follower_count(db, user_id)

    # Send push notification (async, don't wait)
    await send_follow_notification(db, current_user, user_id)

    # Build response
    name = target_user.email
    avatar_url = None
    if target_user.profile:
        name = target_user.profile.full_name or target_user.email
        avatar_url = target_user.profile.profile_picture_url

    return FollowUserResponse(
        id=target_user.id,
        name=name,
        avatar_url=avatar_url,
        is_following=True,
        follower_count=follower_count,
    )


@router.delete("/{user_id}/follow", response_model=FollowUserResponse)
async def unfollow_user(
    user_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Unfollow a user.

    Returns 400 if not following this user.
    """
    # Cannot unfollow yourself
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot unfollow yourself",
        )

    # Check if user exists
    target_result = await db.execute(
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == user_id)
    )
    target_user = target_result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Find the follow relationship
    follow_result = await db.execute(
        select(UserFollow).where(
            UserFollow.follower_id == current_user.id,
            UserFollow.following_id == user_id,
        )
    )
    follow = follow_result.scalar_one_or_none()

    if not follow:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are not following this user",
        )

    # Delete follow relationship
    await db.delete(follow)
    await db.commit()

    # Get updated follower count
    follower_count = await get_follower_count(db, user_id)

    # Build response
    name = target_user.email
    avatar_url = None
    if target_user.profile:
        name = target_user.profile.full_name or target_user.email
        avatar_url = target_user.profile.profile_picture_url

    return FollowUserResponse(
        id=target_user.id,
        name=name,
        avatar_url=avatar_url,
        is_following=False,
        follower_count=follower_count,
    )


@router.get("/{user_id}/followers", response_model=FollowerListResponse)
async def get_user_followers(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get list of users who follow the specified user.
    """
    # Check if user exists
    target_result = await db.execute(
        select(UserAccount).where(UserAccount.id == user_id)
    )
    if not target_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get total count
    count_result = await db.execute(
        select(func.count(UserFollow.id)).where(UserFollow.following_id == user_id)
    )
    total = count_result.scalar() or 0

    # Get paginated followers
    offset = (page - 1) * page_size
    followers_result = await db.execute(
        select(UserFollow)
        .options(selectinload(UserFollow.follower).selectinload(UserAccount.profile))
        .where(UserFollow.following_id == user_id)
        .order_by(UserFollow.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    follows = followers_result.scalars().all()

    # Get IDs of users current user is following (for is_following flag)
    following_ids_result = await db.execute(
        select(UserFollow.following_id).where(
            UserFollow.follower_id == current_user.id
        )
    )
    following_ids = {row[0] for row in following_ids_result.fetchall()}

    # Build response items
    items = []
    for follow in follows:
        follower = follow.follower
        profile = follower.profile

        name = follower.email
        avatar_url = None
        location = None

        if profile:
            name = profile.full_name or follower.email
            avatar_url = profile.profile_picture_url
            if profile.city:
                location = profile.city.name
            elif profile.country:
                location = profile.country.name

        # Get follower count for this user
        fc = await get_follower_count(db, follower.id)

        items.append(
            FollowerListItem(
                id=follower.id,
                name=name,
                avatar_url=avatar_url,
                location=location,
                is_following=follower.id in following_ids,
                follower_count=fc,
            )
        )

    return FollowerListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.get("/{user_id}/following", response_model=FollowerListResponse)
async def get_user_following(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get list of users that the specified user is following.
    """
    # Check if user exists
    target_result = await db.execute(
        select(UserAccount).where(UserAccount.id == user_id)
    )
    if not target_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get total count
    count_result = await db.execute(
        select(func.count(UserFollow.id)).where(UserFollow.follower_id == user_id)
    )
    total = count_result.scalar() or 0

    # Get paginated following
    offset = (page - 1) * page_size
    following_result = await db.execute(
        select(UserFollow)
        .options(selectinload(UserFollow.following).selectinload(UserAccount.profile))
        .where(UserFollow.follower_id == user_id)
        .order_by(UserFollow.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    follows = following_result.scalars().all()

    # Get IDs of users current user is following (for is_following flag)
    following_ids_result = await db.execute(
        select(UserFollow.following_id).where(
            UserFollow.follower_id == current_user.id
        )
    )
    following_ids = {row[0] for row in following_ids_result.fetchall()}

    # Build response items
    items = []
    for follow in follows:
        following = follow.following
        profile = following.profile

        name = following.email
        avatar_url = None
        location = None

        if profile:
            name = profile.full_name or following.email
            avatar_url = profile.profile_picture_url
            if profile.city:
                location = profile.city.name
            elif profile.country:
                location = profile.country.name

        # Get follower count for this user
        fc = await get_follower_count(db, following.id)

        items.append(
            FollowerListItem(
                id=following.id,
                name=name,
                avatar_url=avatar_url,
                location=location,
                is_following=following.id in following_ids,
                follower_count=fc,
            )
        )

    return FollowerListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.get("/{user_id}/follow-stats", response_model=FollowStatsResponse)
async def get_follow_stats(
    user_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get follow statistics for a user.
    """
    # Check if user exists
    target_result = await db.execute(
        select(UserAccount).where(UserAccount.id == user_id)
    )
    if not target_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    follower_count = await get_follower_count(db, user_id)
    following_count = await get_following_count(db, user_id)
    is_following_user = await is_following(db, current_user.id, user_id)

    return FollowStatsResponse(
        follower_count=follower_count,
        following_count=following_count,
        is_following=is_following_user,
    )
