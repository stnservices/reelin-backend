"""Event enrollment endpoints."""

import random
from datetime import datetime, timezone, timedelta
from enum import Enum
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, status

# Time window for post-event actions (disqualification, revalidation)
POST_EVENT_ACTION_HOURS = 72


# Structured error codes for enrollment operations (Story 14.2)
class EnrollmentErrorCode(str, Enum):
    USER_NOT_FOUND = "USER_NOT_FOUND"
    ALREADY_ENROLLED = "ALREADY_ENROLLED"
    USER_BANNED = "USER_BANNED"
    EVENT_FULL = "EVENT_FULL"
    EVENT_NOT_FOUND = "EVENT_NOT_FOUND"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    INVALID_EVENT_STATUS = "INVALID_EVENT_STATUS"
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount
from app.models.event import Event, EventStatus
from app.models.enrollment import EventEnrollment, EnrollmentStatus, EventBan
from app.models.notification import Notification, UserDeviceToken
from app.models.team import TeamMember
from app.models.follow import UserFollow
from app.schemas.enrollment import (
    EnrollmentCreate,
    EnrollmentUpdate,
    EnrollmentResponse,
    EnrollmentDetailResponse,
    EnrollmentListResponse,
    EventBanCreate,
    EventBanResponse,
    DisqualifyRequest,
    ReinstateRequest,
    AdminEnrollRequest,
)
from app.schemas.common import MessageResponse
from app.core.permissions import OrganizerOrAdmin
from app.api.v1.catches import recalculate_user_score
from app.services.push_notifications import send_push_notification

router = APIRouter()


async def randomize_draw_numbers(db: AsyncSession, event_id: int) -> None:
    """
    Randomize draw numbers for all APPROVED and PENDING enrollments of an event.
    Assigns sequential numbers (1, 2, 3...) in random order.
    """
    query = select(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.status.in_([
            EnrollmentStatus.APPROVED.value,
            EnrollmentStatus.PENDING.value,
        ]),
    )
    result = await db.execute(query)
    enrollments = list(result.scalars().all())

    if not enrollments:
        return

    random.shuffle(enrollments)

    for i, enrollment in enumerate(enrollments, start=1):
        enrollment.draw_number = i


def is_within_post_event_window(event: Event) -> bool:
    """
    Check if we're within the allowed time window for post-event actions.
    Returns True if:
    - Event is ongoing (always allowed)
    - Event is finished AND within POST_EVENT_ACTION_HOURS of end_date
    """
    if event.status == EventStatus.ONGOING.value:
        return True

    if event.status == EventStatus.COMPLETED.value:
        if not event.end_date:
            # No end date set, allow action
            return True
        now = datetime.now(timezone.utc)
        end_time = event.end_date
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        deadline = end_time + timedelta(hours=POST_EVENT_ACTION_HOURS)
        return now <= deadline

    return False


def get_post_event_deadline(event: Event) -> datetime | None:
    """Get the deadline for post-event actions."""
    if event.end_date:
        end_time = event.end_date
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        return end_time + timedelta(hours=POST_EVENT_ACTION_HOURS)
    return None


# =============================================================================
# Event Ban Management (MUST be before /{enrollment_id} routes to avoid conflicts)
# =============================================================================


@router.get("/bans/{event_id}", response_model=list[EventBanResponse])
async def list_event_bans(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """
    List all banned users for an event.
    Only organizers and admins can view bans.
    """
    # Check event exists and user has access
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_admin = "administrator" in user_roles
    is_event_owner = event.created_by_id == current_user.id

    if not is_admin and not is_event_owner:
        raise HTTPException(status_code=403, detail="Not authorized to view bans for this event")

    query = (
        select(EventBan)
        .options(
            selectinload(EventBan.user).selectinload(UserAccount.profile),
            selectinload(EventBan.banned_by),
        )
        .where(EventBan.event_id == event_id)
        .order_by(EventBan.banned_at.desc())
    )
    result = await db.execute(query)
    bans = result.scalars().all()

    return [EventBanResponse.from_ban(ban) for ban in bans]


@router.post("/bans/{event_id}", response_model=EventBanResponse, status_code=status.HTTP_201_CREATED)
async def ban_user_from_event(
    event_id: int,
    ban_data: EventBanCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """
    Ban a user from an event.
    Only organizers and admins can ban users.
    Also removes any existing enrollment for this user.
    """
    # Check event exists and user has access
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_admin = "administrator" in user_roles
    is_event_owner = event.created_by_id == current_user.id

    if not is_admin and not is_event_owner:
        raise HTTPException(status_code=403, detail="Not authorized to ban users from this event")

    # Check if already banned
    existing_ban_query = select(EventBan).where(
        EventBan.event_id == event_id,
        EventBan.user_id == ban_data.user_id,
    )
    existing_result = await db.execute(existing_ban_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User is already banned from this event")

    # Remove any existing enrollment
    enrollment_query = select(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.user_id == ban_data.user_id,
    )
    enrollment_result = await db.execute(enrollment_query)
    enrollment = enrollment_result.scalar_one_or_none()
    had_enrollment = enrollment is not None
    if enrollment:
        await db.delete(enrollment)

    # Create ban
    ban = EventBan(
        event_id=event_id,
        user_id=ban_data.user_id,
        banned_by_id=current_user.id,
        reason=ban_data.reason,
    )
    db.add(ban)

    # Randomize draw numbers if an enrollment was removed
    if had_enrollment:
        await randomize_draw_numbers(db, event_id)

    await db.commit()

    # Reload with relationships
    query = (
        select(EventBan)
        .options(
            selectinload(EventBan.user).selectinload(UserAccount.profile),
            selectinload(EventBan.banned_by),
        )
        .where(EventBan.id == ban.id)
    )
    result = await db.execute(query)
    ban = result.scalar_one()

    return EventBanResponse.from_ban(ban)


@router.delete("/bans/{event_id}/{user_id}", response_model=MessageResponse)
async def unban_user_from_event(
    event_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """
    Remove a ban for a user from an event.
    Only organizers and admins can unban users.
    """
    # Check event exists and user has access
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_admin = "administrator" in user_roles
    is_event_owner = event.created_by_id == current_user.id

    if not is_admin and not is_event_owner:
        raise HTTPException(status_code=403, detail="Not authorized to unban users from this event")

    # Find the ban
    ban_query = select(EventBan).where(
        EventBan.event_id == event_id,
        EventBan.user_id == user_id,
    )
    ban_result = await db.execute(ban_query)
    ban = ban_result.scalar_one_or_none()

    if not ban:
        raise HTTPException(status_code=404, detail="Ban not found")

    await db.delete(ban)
    await db.commit()

    return {"message": "User unbanned successfully"}


# =============================================================================
# Enrollment Endpoints
# =============================================================================


@router.get("", response_model=EnrollmentListResponse)
async def list_enrollments(
    event_id: int,
    status_filter: EnrollmentStatus | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    List enrollments for an event.
    Organizers can see all enrollments.
    Regular users can only see their own enrollments.
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check permissions - all authenticated users can see enrollments for published events
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_organizer = bool(user_roles.intersection({"administrator", "organizer"}))
    is_event_owner = event.created_by_id == current_user.id

    # Build base query
    query = (
        select(EventEnrollment)
        .options(
            selectinload(EventEnrollment.user).selectinload(UserAccount.profile),
            selectinload(EventEnrollment.approved_by),
        )
        .where(EventEnrollment.event_id == event_id)
    )

    # All authenticated users can see all enrollments for published/ongoing/completed events
    # For draft events, only organizers and event owners can see enrollments
    if event.status == EventStatus.DRAFT.value and not is_organizer and not is_event_owner:
        query = query.where(EventEnrollment.user_id == current_user.id)

    # Filter by status
    if status_filter:
        query = query.where(EventEnrollment.status == status_filter.value)

    # Get total count
    count_query = select(func.count(EventEnrollment.id)).where(
        EventEnrollment.event_id == event_id
    )
    # Same permission logic as main query
    if event.status == EventStatus.DRAFT.value and not is_organizer and not is_event_owner:
        count_query = count_query.where(EventEnrollment.user_id == current_user.id)
    if status_filter:
        count_query = count_query.where(EventEnrollment.status == status_filter.value)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Pagination
    offset = (page - 1) * page_size
    query = query.order_by(EventEnrollment.enrolled_at.desc()).offset(offset).limit(page_size)

    result = await db.execute(query)
    enrollments = result.scalars().all()

    return EnrollmentListResponse(
        items=[EnrollmentDetailResponse.from_enrollment(e) for e in enrollments],
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.post("", response_model=EnrollmentResponse, status_code=status.HTTP_201_CREATED)
async def create_enrollment(
    enrollment_data: EnrollmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Enroll current user in an event.
    """
    event_id = enrollment_data.event_id

    # Check event exists and is published
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if event.status not in [EventStatus.PUBLISHED.value, EventStatus.ONGOING.value]:
        raise HTTPException(
            status_code=400,
            detail="Event is not open for registration",
        )

    # Check registration deadline
    if event.registration_deadline and datetime.now(timezone.utc) > event.registration_deadline:
        raise HTTPException(
            status_code=400,
            detail="Registration deadline has passed",
        )

    # Check if user has a phone number (required for enrollment)
    if not current_user.profile or not current_user.profile.phone:
        raise HTTPException(
            status_code=400,
            detail="Phone number required to enroll. Please update your profile.",
        )

    # Check if user is banned from this event
    ban_query = select(EventBan).where(
        EventBan.event_id == event_id,
        EventBan.user_id == current_user.id,
    )
    ban_result = await db.execute(ban_query)
    if ban_result.scalar_one_or_none():
        raise HTTPException(
            status_code=403,
            detail="You are banned from this event",
        )

    # Check if already enrolled
    existing_query = select(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.user_id == current_user.id,
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="Already enrolled in this event",
        )

    # Check max participants
    if event.max_participants:
        count_query = select(func.count(EventEnrollment.id)).where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.status.in_([
                EnrollmentStatus.PENDING.value,
                EnrollmentStatus.APPROVED.value,
            ]),
        )
        count_result = await db.execute(count_query)
        current_count = count_result.scalar()

        if current_count >= event.max_participants:
            raise HTTPException(
                status_code=400,
                detail="Event has reached maximum participants",
            )

    # Create enrollment
    initial_status = (
        EnrollmentStatus.PENDING.value if event.requires_approval
        else EnrollmentStatus.APPROVED.value
    )

    enrollment = EventEnrollment(
        event_id=event_id,
        user_id=current_user.id,
        status=initial_status,
    )

    # Auto-approve if not required
    if not event.requires_approval:
        enrollment.approved_at = datetime.now(timezone.utc)

    db.add(enrollment)
    await db.flush()

    # Randomize draw numbers for all active enrollments
    await randomize_draw_numbers(db, event_id)

    # Get user name for notification
    user_name = current_user.email
    if current_user.profile:
        if current_user.profile.first_name or current_user.profile.last_name:
            user_name = f"{current_user.profile.first_name or ''} {current_user.profile.last_name or ''}".strip()

    # Get organizer's device tokens for push notification
    organizer_id = event.created_by_id
    token_query = select(UserDeviceToken.token).where(
        UserDeviceToken.user_id == organizer_id
    )
    token_result = await db.execute(token_query)
    organizer_tokens = list(token_result.scalars().all())

    # Create in-app notification for organizer
    notification = Notification(
        user_id=organizer_id,
        type="new_enrollment",
        title="New Enrollment",
        message=f"{user_name} has enrolled in your event '{event.name}'",
        data={
            "event_id": event_id,
            "event_name": event.name,
            "user_id": current_user.id,
            "user_name": user_name,
        },
    )
    db.add(notification)

    # Get all followers of the enrolled user for notifications
    follower_query = select(UserFollow.follower_id).where(
        UserFollow.following_id == current_user.id
    )
    follower_result = await db.execute(follower_query)
    follower_ids = [row[0] for row in follower_result.fetchall()]

    # Create in-app notifications for all followers
    for follower_id in follower_ids:
        follower_notification = Notification(
            user_id=follower_id,
            type="follower_enrolled_in_event",
            title="Friend Enrolled",
            message=f"{user_name} enrolled in {event.name}",
            data={
                "event_id": event_id,
                "event_name": event.name,
                "user_id": current_user.id,
                "user_name": user_name,
            },
        )
        db.add(follower_notification)

    # Get device tokens for followers (for push notifications after commit)
    follower_tokens = []
    if follower_ids:
        follower_tokens_query = select(UserDeviceToken.token).where(
            UserDeviceToken.user_id.in_(follower_ids)
        )
        follower_tokens_result = await db.execute(follower_tokens_query)
        follower_tokens = list(follower_tokens_result.scalars().all())

    await db.commit()

    # Send push notification to organizer (after commit)
    if organizer_tokens:
        send_push_notification(
            tokens=organizer_tokens,
            title="New Enrollment",
            body=f"{user_name} has enrolled in your event '{event.name}'",
            data={
                "type": "new_enrollment",
                "event_id": str(event_id),
            },
            click_action=f"/events/{event_id}/enrollments",
        )

    # Send push notification to followers (after commit)
    if follower_tokens:
        send_push_notification(
            tokens=follower_tokens,
            title="Friend Enrolled",
            body=f"{user_name} enrolled in {event.name}",
            data={
                "type": "follower_enrolled_in_event",
                "event_id": str(event_id),
                "user_id": str(current_user.id),
            },
            click_action=f"/events/{event_id}",
        )

    await db.refresh(enrollment)

    return enrollment


@router.post("/admin-enroll/{event_id}", response_model=EnrollmentDetailResponse, status_code=status.HTTP_201_CREATED)
async def admin_enroll_user(
    event_id: int,
    request: AdminEnrollRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """
    Admin/organizer enrolls a user into an event by email address.
    - Only event owner or admin can use this endpoint
    - User is looked up by email (case-insensitive)
    - Can directly approve the enrollment (bypasses requires_approval)
    - Phone number NOT required (admin override)
    - Draw number randomization is triggered after enrollment
    """
    # Get event with related data
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=404,
            detail={"code": EnrollmentErrorCode.EVENT_NOT_FOUND, "message": "Event not found"}
        )

    # Check authorization - must be event owner or admin
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_admin = "administrator" in user_roles
    is_event_owner = event.created_by_id == current_user.id

    if not is_admin and not is_event_owner:
        raise HTTPException(
            status_code=403,
            detail={"code": EnrollmentErrorCode.NOT_AUTHORIZED, "message": "Not authorized to admin-enroll users for this event"}
        )

    # Validate event status
    if event.status in [EventStatus.COMPLETED.value, EventStatus.CANCELLED.value]:
        raise HTTPException(
            status_code=400,
            detail={"code": EnrollmentErrorCode.INVALID_EVENT_STATUS, "message": "Cannot enroll users in completed or cancelled events"}
        )

    # Team event restriction: only DRAFT or PUBLISHED (not ONGOING)
    if event.is_team_event and event.status == EventStatus.ONGOING.value:
        raise HTTPException(
            status_code=400,
            detail={"code": EnrollmentErrorCode.INVALID_EVENT_STATUS, "message": "Cannot admin-enroll users in ongoing team events. Enroll before event starts."}
        )

    # Find user by email (case-insensitive)
    user_query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(func.lower(UserAccount.email) == request.user_email.lower())
        .where(UserAccount.is_active == True)  # noqa: E712
    )
    user_result = await db.execute(user_query)
    target_user = user_result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=404,
            detail={"code": EnrollmentErrorCode.USER_NOT_FOUND, "message": f"User with email '{request.user_email}' not found"}
        )

    # Check if already enrolled
    existing_query = select(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.user_id == target_user.id,
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail={"code": EnrollmentErrorCode.ALREADY_ENROLLED, "message": "User is already enrolled in this event"}
        )

    # Check if user is banned from this event
    ban_query = select(EventBan).where(
        EventBan.event_id == event_id,
        EventBan.user_id == target_user.id,
    )
    ban_result = await db.execute(ban_query)
    if ban_result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail={"code": EnrollmentErrorCode.USER_BANNED, "message": "User is banned from this event"}
        )

    # Check capacity (if max_participants set)
    if event.max_participants:
        count_query = select(func.count(EventEnrollment.id)).where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.status.in_([
                EnrollmentStatus.PENDING.value,
                EnrollmentStatus.APPROVED.value,
            ]),
        )
        count_result = await db.execute(count_query)
        current_count = count_result.scalar()

        if current_count >= event.max_participants:
            raise HTTPException(
                status_code=400,
                detail={"code": EnrollmentErrorCode.EVENT_FULL, "message": "Event has reached maximum capacity"}
            )

    # Create enrollment
    enrollment_status = (
        EnrollmentStatus.APPROVED.value
        if request.approve_immediately
        else EnrollmentStatus.PENDING.value
    )

    enrollment = EventEnrollment(
        event_id=event_id,
        user_id=target_user.id,
        status=enrollment_status,
    )

    if request.approve_immediately:
        enrollment.approved_by_id = current_user.id
        enrollment.approved_at = datetime.now(timezone.utc)

    db.add(enrollment)
    await db.flush()

    # Randomize draw numbers for all active enrollments
    await randomize_draw_numbers(db, event_id)

    await db.commit()

    # Reload with relationships
    query = (
        select(EventEnrollment)
        .options(
            selectinload(EventEnrollment.user).selectinload(UserAccount.profile),
            selectinload(EventEnrollment.approved_by),
        )
        .where(EventEnrollment.id == enrollment.id)
    )
    result = await db.execute(query)
    enrollment = result.scalar_one()

    return EnrollmentDetailResponse.from_enrollment(enrollment)


@router.get("/{enrollment_id}", response_model=EnrollmentDetailResponse)
async def get_enrollment(
    enrollment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get a specific enrollment.
    """
    query = (
        select(EventEnrollment)
        .options(
            selectinload(EventEnrollment.user).selectinload(UserAccount.profile),
            selectinload(EventEnrollment.approved_by),
            selectinload(EventEnrollment.event),
        )
        .where(EventEnrollment.id == enrollment_id)
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    # Check permissions
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_organizer = bool(user_roles.intersection({"administrator", "organizer"}))
    is_event_owner = enrollment.event.created_by_id == current_user.id
    is_own_enrollment = enrollment.user_id == current_user.id

    if not is_organizer and not is_event_owner and not is_own_enrollment:
        raise HTTPException(status_code=403, detail="Not authorized to view this enrollment")

    return EnrollmentDetailResponse.from_enrollment(enrollment)


@router.patch("/{enrollment_id}", response_model=EnrollmentDetailResponse | MessageResponse)
async def update_enrollment(
    enrollment_id: int,
    update_data: EnrollmentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """
    Update enrollment status (approve/reject).
    Only organizers and admins can update enrollments.

    Note: Rejection will DELETE the enrollment and send a push notification to the user.
    """
    query = (
        select(EventEnrollment)
        .options(
            selectinload(EventEnrollment.user).selectinload(UserAccount.profile),
            selectinload(EventEnrollment.approved_by),
            selectinload(EventEnrollment.event),
            selectinload(EventEnrollment.team_membership).selectinload(TeamMember.team),
        )
        .where(EventEnrollment.id == enrollment_id)
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    # Check if user has permission for this event
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_admin = "administrator" in user_roles
    is_event_owner = enrollment.event.created_by_id == current_user.id

    if not is_admin and not is_event_owner:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to manage this event's enrollments",
        )

    # Handle REJECTION specially - delete enrollment and notify user
    if update_data.status == EnrollmentStatus.REJECTED:
        user_id = enrollment.user_id
        event_id = enrollment.event_id
        event_name = enrollment.event.name
        user_name = enrollment.user.profile.full_name if enrollment.user.profile else "User"
        rejection_reason = update_data.rejection_reason or "No reason provided"
        team_name = enrollment.team_membership.team.name if enrollment.team_membership else None

        # Create in-app notification
        notification = Notification(
            user_id=user_id,
            type="enrollment_rejected",
            title="Enrollment Rejected",
            message=f"Your enrollment for '{event_name}' has been rejected. Reason: {rejection_reason}",
            data={
                "event_id": event_id,
                "event_name": event_name,
                "rejection_reason": rejection_reason,
            },
        )
        db.add(notification)

        # Get user's device tokens for push notification
        token_query = select(UserDeviceToken.token).where(
            UserDeviceToken.user_id == user_id
        )
        token_result = await db.execute(token_query)
        tokens = list(token_result.scalars().all())

        # Delete the enrollment
        await db.delete(enrollment)

        # Randomize draw numbers for remaining enrollments
        await randomize_draw_numbers(db, event_id)

        await db.commit()

        # Send push notification (after commit so notification is saved)
        if tokens:
            send_push_notification(
                tokens=tokens,
                title="Enrollment Rejected",
                body=f"Your enrollment for '{event_name}' has been rejected.",
                data={
                    "type": "enrollment_rejected",
                    "event_id": str(event_id),
                },
                click_action=f"/events/{event_id}",
            )

        # Include team removal info if applicable
        if team_name:
            return {"message": f"Enrollment rejected and removed. User {user_name} has been removed from team '{team_name}' and notified."}
        return {"message": f"Enrollment rejected and removed. User {user_name} has been notified."}

    # Update status for non-rejection cases
    old_status = enrollment.status
    enrollment.status = update_data.status.value

    if update_data.draw_number is not None:
        enrollment.draw_number = update_data.draw_number

    # Track if this is an approval for notification
    is_new_approval = (
        update_data.status == EnrollmentStatus.APPROVED
        and old_status != EnrollmentStatus.APPROVED.value
    )

    if is_new_approval:
        enrollment.approved_by_id = current_user.id
        enrollment.approved_at = datetime.now(timezone.utc)

    # Store data for notifications before commit
    user_id = enrollment.user_id
    event_id = enrollment.event_id
    event_name = enrollment.event.name

    # Get user's device tokens for push notification (if approval)
    tokens = []
    if is_new_approval:
        token_query = select(UserDeviceToken.token).where(
            UserDeviceToken.user_id == user_id
        )
        token_result = await db.execute(token_query)
        tokens = list(token_result.scalars().all())

        # Create in-app notification for approved user
        notification = Notification(
            user_id=user_id,
            type="enrollment_approved",
            title="Enrollment Approved",
            message=f"Your enrollment for '{event_name}' has been approved!",
            data={
                "event_id": event_id,
                "event_name": event_name,
            },
        )
        db.add(notification)

    # Note: We do NOT randomize draw numbers on approval.
    # Draw numbers are assigned when users enroll (pending gets a number too).
    # We only re-randomize when the count changes (reject, delete, disqualify, reinstate).

    await db.commit()

    # Send push notification for approval (after commit)
    if is_new_approval and tokens:
        send_push_notification(
            tokens=tokens,
            title="Enrollment Approved",
            body=f"Your enrollment for '{event_name}' has been approved!",
            data={
                "type": "enrollment_approved",
                "event_id": str(event_id),
            },
            click_action=f"/events/{event_id}",
        )

    await db.refresh(enrollment)

    # Reload relationships
    query = (
        select(EventEnrollment)
        .options(
            selectinload(EventEnrollment.user).selectinload(UserAccount.profile),
            selectinload(EventEnrollment.approved_by),
        )
        .where(EventEnrollment.id == enrollment_id)
    )
    result = await db.execute(query)
    enrollment = result.scalar_one()

    return EnrollmentDetailResponse.from_enrollment(enrollment)


@router.delete("/{enrollment_id}", response_model=MessageResponse)
async def delete_enrollment(
    enrollment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Delete/cancel an enrollment.
    - Users can cancel their own pending enrollments (sets status to cancelled).
    - Organizers/admins can DELETE any enrollment for their events (actually removes from DB).
    - BLOCKED: Cannot remove enrollments for ongoing or finished events (use disqualification instead).
    """
    query = (
        select(EventEnrollment)
        .options(selectinload(EventEnrollment.event))
        .where(EventEnrollment.id == enrollment_id)
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    # Check permissions
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_admin = "administrator" in user_roles
    is_event_owner = enrollment.event.created_by_id == current_user.id
    is_own_enrollment = enrollment.user_id == current_user.id

    # Block removal for ongoing/finished events
    if enrollment.event.status in [EventStatus.ONGOING.value, EventStatus.COMPLETED.value]:
        raise HTTPException(
            status_code=400,
            detail="Cannot remove enrollments for ongoing or finished events. Use disqualification instead.",
        )

    if is_admin or is_event_owner:
        # Organizers/admins can fully delete the enrollment
        event_id = enrollment.event_id
        await db.delete(enrollment)
        # Randomize draw numbers for remaining active enrollments
        await randomize_draw_numbers(db, event_id)
        await db.commit()
        return {"message": "Enrollment removed successfully"}
    elif is_own_enrollment:
        # Users can only cancel pending enrollments
        if enrollment.status != EnrollmentStatus.PENDING.value:
            raise HTTPException(
                status_code=400,
                detail="Can only cancel pending enrollments",
            )
        enrollment.status = EnrollmentStatus.CANCELLED.value
        # Randomize draw numbers for remaining active enrollments
        await randomize_draw_numbers(db, enrollment.event_id)
        await db.commit()
        return {"message": "Enrollment cancelled successfully"}
    else:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to delete this enrollment",
        )


@router.post("/{enrollment_id}/assign-number", response_model=EnrollmentDetailResponse)
async def assign_draw_number(
    enrollment_id: int,
    draw_number: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """
    Assign a draw number to an approved enrollment.
    """
    query = (
        select(EventEnrollment)
        .options(
            selectinload(EventEnrollment.user).selectinload(UserAccount.profile),
            selectinload(EventEnrollment.approved_by),
            selectinload(EventEnrollment.event),
        )
        .where(EventEnrollment.id == enrollment_id)
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    if enrollment.status != EnrollmentStatus.APPROVED.value:
        raise HTTPException(
            status_code=400,
            detail="Can only assign draw numbers to approved enrollments",
        )

    # Check if draw number is already taken
    existing_query = select(EventEnrollment).where(
        EventEnrollment.event_id == enrollment.event_id,
        EventEnrollment.draw_number == draw_number,
        EventEnrollment.id != enrollment_id,
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Draw number {draw_number} is already assigned",
        )

    enrollment.draw_number = draw_number
    await db.commit()
    await db.refresh(enrollment)

    return EnrollmentDetailResponse.from_enrollment(enrollment)


# =============================================================================
# Disqualification Endpoints
# =============================================================================


@router.post("/{enrollment_id}/disqualify", response_model=EnrollmentDetailResponse)
async def disqualify_participant(
    enrollment_id: int,
    disqualify_data: DisqualifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """
    Disqualify a participant from an event.
    - Only available for ongoing or finished events
    - Requires a mandatory reason
    - Excludes participant's catches from rankings
    - Triggers scoreboard recalculation
    """
    query = (
        select(EventEnrollment)
        .options(
            selectinload(EventEnrollment.user).selectinload(UserAccount.profile),
            selectinload(EventEnrollment.approved_by),
            selectinload(EventEnrollment.disqualified_by),
            selectinload(EventEnrollment.reinstated_by),
            selectinload(EventEnrollment.event),
        )
        .where(EventEnrollment.id == enrollment_id)
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    # Check if user has permission for this event
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_admin = "administrator" in user_roles
    is_event_owner = enrollment.event.created_by_id == current_user.id

    if not is_admin and not is_event_owner:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to disqualify participants from this event",
        )

    # Check event status - only allow for ongoing or finished events (within 72h window)
    if enrollment.event.status not in [EventStatus.ONGOING.value, EventStatus.COMPLETED.value]:
        raise HTTPException(
            status_code=400,
            detail="Disqualification is only available for ongoing or finished events",
        )

    # Check 72-hour window for finished events
    if not is_within_post_event_window(enrollment.event):
        deadline = get_post_event_deadline(enrollment.event)
        raise HTTPException(
            status_code=400,
            detail=f"Disqualification window has expired. Actions are only allowed within {POST_EVENT_ACTION_HOURS} hours after event ends."
            + (f" Deadline was: {deadline.isoformat()}" if deadline else ""),
        )

    # Check if already disqualified
    if enrollment.status == EnrollmentStatus.DISQUALIFIED.value:
        raise HTTPException(
            status_code=400,
            detail="Participant is already disqualified",
        )

    # Check if enrollment was approved (can only disqualify approved participants)
    if enrollment.status != EnrollmentStatus.APPROVED.value:
        raise HTTPException(
            status_code=400,
            detail="Can only disqualify approved participants",
        )

    # Update enrollment
    enrollment.status = EnrollmentStatus.DISQUALIFIED.value
    enrollment.disqualified_by_id = current_user.id
    enrollment.disqualified_at = datetime.now(timezone.utc)
    enrollment.disqualification_reason = disqualify_data.reason
    # Clear any previous reinstatement data
    enrollment.reinstated_by_id = None
    enrollment.reinstated_at = None
    enrollment.reinstatement_reason = None

    await db.flush()

    # Trigger scoreboard recalculation to exclude this user's catches
    await recalculate_user_score(db, enrollment.event_id, enrollment.user_id)

    # Note: We do NOT randomize draw numbers on disqualify.
    # The user keeps their draw number (they're still in enrollments, just disqualified).

    await db.commit()

    # Reload relationships
    query = (
        select(EventEnrollment)
        .options(
            selectinload(EventEnrollment.user).selectinload(UserAccount.profile),
            selectinload(EventEnrollment.approved_by),
            selectinload(EventEnrollment.disqualified_by),
            selectinload(EventEnrollment.reinstated_by),
        )
        .where(EventEnrollment.id == enrollment_id)
    )
    result = await db.execute(query)
    enrollment = result.scalar_one()

    return EnrollmentDetailResponse.from_enrollment(enrollment)


@router.post("/{enrollment_id}/reinstate", response_model=EnrollmentDetailResponse)
async def reinstate_participant(
    enrollment_id: int,
    reinstate_data: ReinstateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """
    Reinstate a disqualified participant.
    - Restores status to APPROVED
    - Records reinstatement details
    - Triggers scoreboard recalculation to include catches again
    """
    query = (
        select(EventEnrollment)
        .options(
            selectinload(EventEnrollment.user).selectinload(UserAccount.profile),
            selectinload(EventEnrollment.approved_by),
            selectinload(EventEnrollment.disqualified_by),
            selectinload(EventEnrollment.reinstated_by),
            selectinload(EventEnrollment.event),
        )
        .where(EventEnrollment.id == enrollment_id)
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    # Check if user has permission for this event
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_admin = "administrator" in user_roles
    is_event_owner = enrollment.event.created_by_id == current_user.id

    if not is_admin and not is_event_owner:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to reinstate participants for this event",
        )

    # Check 72-hour window for finished events
    if not is_within_post_event_window(enrollment.event):
        deadline = get_post_event_deadline(enrollment.event)
        raise HTTPException(
            status_code=400,
            detail=f"Reinstatement window has expired. Actions are only allowed within {POST_EVENT_ACTION_HOURS} hours after event ends."
            + (f" Deadline was: {deadline.isoformat()}" if deadline else ""),
        )

    # Check if currently disqualified
    if enrollment.status != EnrollmentStatus.DISQUALIFIED.value:
        raise HTTPException(
            status_code=400,
            detail="Can only reinstate disqualified participants",
        )

    # Update enrollment
    enrollment.status = EnrollmentStatus.APPROVED.value
    enrollment.reinstated_by_id = current_user.id
    enrollment.reinstated_at = datetime.now(timezone.utc)
    enrollment.reinstatement_reason = reinstate_data.reason

    await db.flush()

    # Trigger scoreboard recalculation to include this user's catches again
    await recalculate_user_score(db, enrollment.event_id, enrollment.user_id)

    # Note: We do NOT randomize draw numbers on reinstate.
    # The user keeps their original draw number.

    await db.commit()

    # Reload relationships
    query = (
        select(EventEnrollment)
        .options(
            selectinload(EventEnrollment.user).selectinload(UserAccount.profile),
            selectinload(EventEnrollment.approved_by),
            selectinload(EventEnrollment.disqualified_by),
            selectinload(EventEnrollment.reinstated_by),
        )
        .where(EventEnrollment.id == enrollment_id)
    )
    result = await db.execute(query)
    enrollment = result.scalar_one()

    return EnrollmentDetailResponse.from_enrollment(enrollment)
