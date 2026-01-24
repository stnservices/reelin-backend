"""Admin notification endpoints for sending targeted push notifications."""

import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.permissions import OrganizerOrAdmin, AdminOnly
from app.models.user import UserAccount, UserProfile
from app.models.event import Event
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.club import Club, ClubMembership, MembershipStatus
from app.models.notification import UserDeviceToken
from app.schemas.notification import (
    AudienceType,
    TargetedNotificationRequest,
    TargetedNotificationResponse,
)
from app.tasks.notifications import send_notification_to_users

router = APIRouter()

# Simple in-memory rate limiting for organizer notifications
# Format: {user_id: [timestamp1, timestamp2, ...]}
_organizer_notification_times: dict[int, list[float]] = {}


def _is_admin(current_user: UserAccount) -> bool:
    """Check if user is an administrator."""
    if not current_user.profile:
        return False
    return "administrator" in (current_user.profile.roles or [])


def _is_organizer(current_user: UserAccount) -> bool:
    """Check if user is an organizer."""
    if not current_user.profile:
        return False
    return "organizer" in (current_user.profile.roles or [])


def _check_organizer_rate_limit(user_id: int) -> tuple[bool, int]:
    """
    Check if organizer has exceeded rate limit (5/hour).
    Returns (is_allowed, remaining_count).
    """
    current_time = time.time()
    one_hour_ago = current_time - 3600

    # Clean up old entries
    if user_id in _organizer_notification_times:
        _organizer_notification_times[user_id] = [
            t for t in _organizer_notification_times[user_id] if t > one_hour_ago
        ]
    else:
        _organizer_notification_times[user_id] = []

    recent_count = len(_organizer_notification_times[user_id])
    remaining = max(0, 5 - recent_count)

    if recent_count >= 5:
        return False, 0

    return True, remaining


def _record_organizer_notification(user_id: int):
    """Record a notification send for rate limiting."""
    current_time = time.time()
    if user_id not in _organizer_notification_times:
        _organizer_notification_times[user_id] = []
    _organizer_notification_times[user_id].append(current_time)


async def _get_individual_user_id(
    db: AsyncSession,
    user_id: int | None,
    user_email: str | None,
) -> int | None:
    """Get user ID by ID or email."""
    if user_id:
        result = await db.execute(
            select(UserAccount.id).where(UserAccount.id == user_id)
        )
        return result.scalar_one_or_none()

    if user_email:
        result = await db.execute(
            select(UserAccount.id).where(UserAccount.email == user_email)
        )
        return result.scalar_one_or_none()

    return None


async def _get_event_participant_ids(
    db: AsyncSession,
    event_id: int,
) -> List[int]:
    """Get user IDs of approved event participants."""
    result = await db.execute(
        select(EventEnrollment.user_id).where(
            and_(
                EventEnrollment.event_id == event_id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
            )
        )
    )
    return list(result.scalars().all())


async def _get_club_member_ids(
    db: AsyncSession,
    club_id: int,
) -> List[int]:
    """Get user IDs of active club members."""
    result = await db.execute(
        select(ClubMembership.user_id).where(
            and_(
                ClubMembership.club_id == club_id,
                ClubMembership.status == MembershipStatus.ACTIVE.value,
            )
        )
    )
    return list(result.scalars().all())


async def _get_all_organizer_ids(db: AsyncSession) -> List[int]:
    """Get user IDs of all organizers."""
    result = await db.execute(
        select(UserProfile.user_id).where(
            UserProfile.roles.contains(["organizer"])
        )
    )
    return list(result.scalars().all())


async def _get_all_users_with_tokens(db: AsyncSession) -> List[int]:
    """Get user IDs of all users that have at least one push notification token."""
    result = await db.execute(
        select(UserDeviceToken.user_id).distinct()
    )
    return list(result.scalars().all())


async def _validate_organizer_event_access(
    db: AsyncSession,
    current_user: UserAccount,
    event_id: int,
) -> Event | None:
    """Validate organizer owns the event. Returns event if valid."""
    result = await db.execute(
        select(Event).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()

    if not event:
        return None

    # Organizers can only access their own events
    if event.created_by_id != current_user.id and not _is_admin(current_user):
        return None

    return event


@router.post(
    "/send",
    response_model=TargetedNotificationResponse,
    summary="Send targeted push notification",
    description="Send push notification to targeted audience. Organizers limited to 5/hour and their own events.",
)
async def send_targeted_notification(
    request: Request,
    notification: TargetedNotificationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """
    Send a targeted push notification.

    Permissions:
    - Admin: Can send to any audience, no rate limit
    - Organizer: Can only send to their event participants, 5/hour limit

    Audience types:
    - individual: Single user by ID or email
    - event_participants: All approved enrollments for an event
    - club_members: All active members of a club
    - all_organizers: All users with organizer role (admin only)
    """
    is_admin = _is_admin(current_user)

    # Check rate limit for non-admins
    if not is_admin:
        allowed, remaining = _check_organizer_rate_limit(current_user.id)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Organizers can send up to 5 notifications per hour.",
            )

    # Validate audience restrictions for organizers
    if not is_admin:
        # Organizers can only send to event_participants
        if notification.audience_type != AudienceType.EVENT_PARTICIPANTS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organizers can only send notifications to their event participants",
            )

        if not notification.audience_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Event ID required for event_participants audience",
            )

        # Validate organizer owns the event
        event = await _validate_organizer_event_access(
            db, current_user, notification.audience_id
        )
        if not event:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only send notifications to participants of your own events",
            )

    # Get target user IDs based on audience type
    user_ids: List[int] = []

    if notification.audience_type == AudienceType.INDIVIDUAL:
        user_id = await _get_individual_user_id(
            db, notification.audience_id, notification.user_email
        )
        if user_id:
            user_ids = [user_id]
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

    elif notification.audience_type == AudienceType.EVENT_PARTICIPANTS:
        if not notification.audience_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Event ID required",
            )
        # Verify event exists
        result = await db.execute(
            select(Event).where(Event.id == notification.audience_id)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event not found",
            )
        user_ids = await _get_event_participant_ids(db, notification.audience_id)

    elif notification.audience_type == AudienceType.CLUB_MEMBERS:
        if not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators can send to club members",
            )
        if not notification.audience_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Club ID required",
            )
        # Verify club exists
        result = await db.execute(
            select(Club).where(Club.id == notification.audience_id)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Club not found",
            )
        user_ids = await _get_club_member_ids(db, notification.audience_id)

    elif notification.audience_type == AudienceType.ALL_ORGANIZERS:
        if not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators can send to all organizers",
            )
        user_ids = await _get_all_organizer_ids(db)

    elif notification.audience_type == AudienceType.ALL_USERS:
        if not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators can send to all users",
            )
        user_ids = await _get_all_users_with_tokens(db)

    if not user_ids:
        return TargetedNotificationResponse(
            success=True,
            message="No recipients found for this audience",
            recipient_count=0,
        )

    # Queue the notification task
    task = send_notification_to_users.delay(
        user_ids=user_ids,
        title=notification.title,
        body=notification.body,
        data=notification.data,
    )

    # Record the notification for rate limiting (non-admins only)
    if not is_admin:
        _record_organizer_notification(current_user.id)

    return TargetedNotificationResponse(
        success=True,
        message=f"Notification queued for {len(user_ids)} recipient(s)",
        recipient_count=len(user_ids),
        task_id=task.id,
    )


@router.get(
    "/recipient-count",
    summary="Get recipient count for audience",
    description="Preview how many users would receive a notification for given audience.",
)
async def get_recipient_count(
    audience_type: AudienceType,
    audience_id: int | None = None,
    user_email: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """Get the count of recipients for a given audience without sending."""
    is_admin = _is_admin(current_user)

    # Same permission checks as send
    if not is_admin and audience_type != AudienceType.EVENT_PARTICIPANTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organizers can only view counts for their event participants",
        )

    if not is_admin and audience_id:
        event = await _validate_organizer_event_access(db, current_user, audience_id)
        if not event:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view participant counts for your own events",
            )

    count = 0

    if audience_type == AudienceType.INDIVIDUAL:
        user_id = await _get_individual_user_id(db, audience_id, user_email)
        count = 1 if user_id else 0

    elif audience_type == AudienceType.EVENT_PARTICIPANTS:
        if audience_id:
            user_ids = await _get_event_participant_ids(db, audience_id)
            count = len(user_ids)

    elif audience_type == AudienceType.CLUB_MEMBERS:
        if not is_admin:
            raise HTTPException(status_code=403, detail="Admin only")
        if audience_id:
            user_ids = await _get_club_member_ids(db, audience_id)
            count = len(user_ids)

    elif audience_type == AudienceType.ALL_ORGANIZERS:
        if not is_admin:
            raise HTTPException(status_code=403, detail="Admin only")
        user_ids = await _get_all_organizer_ids(db)
        count = len(user_ids)

    elif audience_type == AudienceType.ALL_USERS:
        if not is_admin:
            raise HTTPException(status_code=403, detail="Admin only")
        user_ids = await _get_all_users_with_tokens(db)
        count = len(user_ids)

    return {"recipient_count": count}
