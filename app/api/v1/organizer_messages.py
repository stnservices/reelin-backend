"""Organizer message endpoints for contact form."""

from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount
from app.models.event import Event, EventStatus
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.organizer_message import OrganizerMessage
from app.schemas.organizer_message import (
    OrganizerMessageCreate,
    OrganizerMessageResponse,
    OrganizerMessageListResponse,
    OrganizerMessageSendResponse,
    UnreadCountResponse,
)
from app.services.email import EmailService

router = APIRouter()

# Rate limit: 1 message per event per day
RATE_LIMIT_HOURS = 24


async def check_rate_limit(db: AsyncSession, event_id: int, sender_id: int) -> bool:
    """
    Check if user can send a message (rate limit: 1 per event per day).
    Returns True if allowed, False if rate limited.
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=RATE_LIMIT_HOURS)

    query = select(OrganizerMessage).where(
        OrganizerMessage.event_id == event_id,
        OrganizerMessage.sender_id == sender_id,
        OrganizerMessage.created_at >= cutoff_time,
    )
    result = await db.execute(query)
    existing = result.scalar_one_or_none()

    return existing is None


async def is_user_enrolled(db: AsyncSession, event_id: int, user_id: int) -> bool:
    """Check if user is enrolled (any status) in the event."""
    query = select(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.user_id == user_id,
    )
    result = await db.execute(query)
    return result.scalar_one_or_none() is not None


def send_organizer_notification(
    organizer_email: str,
    organizer_name: str,
    event_name: str,
    sender_name: str,
    sender_email: str,
    sender_phone: Optional[str],
    is_enrolled: bool,
    subject: str,
    message: str,
) -> bool:
    """Send email notification to organizer about new message."""
    try:
        email_service = EmailService()
        if not email_service.is_configured():
            return False

        # Build enrollment status text
        enrollment_status = "Enrolled participant" if is_enrolled else "Not enrolled"

        # Build HTML content
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>New message about {event_name}</title>
</head>
<body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; margin: 0; padding: 0; background-color: #f4f4f4;">
    <table cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #f4f4f4;">
        <tr>
            <td style="padding: 20px;">
                <table cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <!-- Header -->
                    <tr>
                        <td style="padding: 40px 30px; text-align: center; background-color: #ffc107; border-radius: 8px 8px 0 0;">
                            <img src="https://cdn.reelin.ro/legacy/media/assets/logo_orange_yellow_transparent.png" alt="ReelIn Logo" width="120" style="margin-bottom: 10px;">
                            <h1 style="color: #000000; font-size: 24px; margin: 0;">
                                New Message Received
                            </h1>
                        </td>
                    </tr>
                    <!-- Content -->
                    <tr>
                        <td style="padding: 30px;">
                            <p style="font-size: 16px; line-height: 1.5; color: #333333; margin-bottom: 20px;">
                                Hi {organizer_name},
                            </p>
                            <p style="font-size: 16px; line-height: 1.5; color: #333333; margin-bottom: 20px;">
                                You've received a new message about your event <strong>{event_name}</strong>.
                            </p>

                            <!-- Sender Info Box -->
                            <table cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #f8f9fa; border-radius: 8px; margin: 20px 0;">
                                <tr>
                                    <td style="padding: 20px;">
                                        <p style="font-size: 14px; color: #666666; margin: 0 0 5px 0;"><strong>From:</strong> {sender_name}</p>
                                        <p style="font-size: 14px; color: #666666; margin: 0 0 5px 0;"><strong>Email:</strong> <a href="mailto:{sender_email}" style="color: #007bff;">{sender_email}</a></p>
                                        {f'<p style="font-size: 14px; color: #666666; margin: 0 0 5px 0;"><strong>Phone:</strong> {sender_phone}</p>' if sender_phone else ''}
                                        <p style="font-size: 14px; color: #666666; margin: 0;"><strong>Status:</strong> {enrollment_status}</p>
                                    </td>
                                </tr>
                            </table>

                            <!-- Message Box -->
                            <table cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #ffffff; border: 1px solid #dee2e6; border-radius: 8px; margin: 20px 0;">
                                <tr>
                                    <td style="padding: 20px;">
                                        <p style="font-size: 16px; font-weight: bold; color: #333333; margin: 0 0 10px 0;">{subject}</p>
                                        <p style="font-size: 14px; line-height: 1.6; color: #333333; margin: 0; white-space: pre-wrap;">{message}</p>
                                    </td>
                                </tr>
                            </table>

                            <p style="font-size: 14px; line-height: 1.5; color: #666666; margin-top: 20px;">
                                You can reply directly to the sender by emailing them at <a href="mailto:{sender_email}" style="color: #007bff;">{sender_email}</a>.
                            </p>
                        </td>
                    </tr>
                    <!-- Footer -->
                    <tr>
                        <td style="padding: 20px 30px; background-color: #f8f9fa; border-radius: 0 0 8px 8px; text-align: center;">
                            <p style="font-size: 12px; color: #999999; margin: 0;">
                                This email was sent from ReelIn. You're receiving this because you're the organizer of {event_name}.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""

        return email_service.send_email(
            to_email=organizer_email,
            subject=f"New message about {event_name}: {subject}",
            html_content=html_content,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to send organizer notification: {e}")
        return False


@router.post(
    "/events/{event_id}/contact-organizer",
    response_model=OrganizerMessageSendResponse,
    status_code=status.HTTP_201_CREATED,
)
async def contact_organizer(
    event_id: int,
    message_data: OrganizerMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Send a message to the event organizer.

    - Any logged-in user can contact organizers
    - Rate limited to 1 message per event per day
    - Sender's name, email, and phone are shared with the organizer
    """
    # Get event with organizer info
    event_query = (
        select(Event)
        .options(selectinload(Event.created_by).selectinload(UserAccount.profile))
        .where(Event.id == event_id, Event.is_deleted == False)
    )
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Don't allow contacting organizers of draft events
    if event.status == EventStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="Cannot contact organizer of draft events")

    # Check rate limit
    if not await check_rate_limit(db, event_id, current_user.id):
        raise HTTPException(
            status_code=429,
            detail="You can only send one message per event per day. Please try again later.",
        )

    # Get sender profile info
    sender_profile = current_user.profile
    if not sender_profile:
        raise HTTPException(status_code=400, detail="User profile not found")

    sender_name = sender_profile.full_name
    sender_email = current_user.email
    sender_phone = sender_profile.phone

    # Check enrollment status
    is_enrolled = await is_user_enrolled(db, event_id, current_user.id)

    # Create message
    org_message = OrganizerMessage(
        event_id=event_id,
        sender_id=current_user.id,
        subject=message_data.subject,
        message=message_data.message,
        sender_name=sender_name,
        sender_email=sender_email,
        sender_phone=sender_phone,
        is_enrolled=is_enrolled,
    )

    db.add(org_message)
    await db.commit()

    # Send email notification to organizer
    organizer = event.created_by
    organizer_profile = organizer.profile if organizer else None
    organizer_name = organizer_profile.first_name if organizer_profile else "Organizer"
    organizer_email = organizer.email if organizer else None

    if organizer_email:
        send_organizer_notification(
            organizer_email=organizer_email,
            organizer_name=organizer_name,
            event_name=event.name,
            sender_name=sender_name,
            sender_email=sender_email,
            sender_phone=sender_phone,
            is_enrolled=is_enrolled,
            subject=message_data.subject,
            message=message_data.message,
        )

    return OrganizerMessageSendResponse(success=True, message="Message sent successfully")


@router.get("/organizer/messages", response_model=OrganizerMessageListResponse)
async def list_organizer_messages(
    event_id: Optional[int] = Query(None, description="Filter by event ID"),
    is_read: Optional[bool] = Query(None, description="Filter by read status"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    List messages sent to the current user's events.

    Only organizers can access this endpoint.
    """
    # Check if user is an organizer
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_organizer = "organizer" in user_roles
    is_admin = "administrator" in user_roles

    if not is_organizer and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only organizers can access messages inbox",
        )

    # Get events created by this user
    events_query = select(Event.id).where(
        Event.created_by_id == current_user.id,
        Event.is_deleted == False,
    )
    events_result = await db.execute(events_query)
    event_ids = [row[0] for row in events_result.all()]

    if not event_ids:
        return OrganizerMessageListResponse(
            items=[],
            total=0,
            page=page,
            per_page=per_page,
            pages=1,
        )

    # Build query for messages
    query = (
        select(OrganizerMessage)
        .options(selectinload(OrganizerMessage.event))
        .where(OrganizerMessage.event_id.in_(event_ids))
    )

    # Apply filters
    if event_id is not None:
        if event_id not in event_ids:
            raise HTTPException(status_code=403, detail="Not authorized to view messages for this event")
        query = query.where(OrganizerMessage.event_id == event_id)

    if is_read is not None:
        query = query.where(OrganizerMessage.is_read == is_read)

    # Count total
    count_query = select(func.count(OrganizerMessage.id)).where(
        OrganizerMessage.event_id.in_(event_ids)
    )
    if event_id is not None:
        count_query = count_query.where(OrganizerMessage.event_id == event_id)
    if is_read is not None:
        count_query = count_query.where(OrganizerMessage.is_read == is_read)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Pagination
    offset = (page - 1) * per_page
    query = query.order_by(OrganizerMessage.created_at.desc()).offset(offset).limit(per_page)

    result = await db.execute(query)
    messages = result.scalars().all()

    pages = ceil(total / per_page) if total > 0 else 1

    # Build response items
    items = [
        OrganizerMessageResponse(
            id=msg.id,
            event_id=msg.event_id,
            event_name=msg.event.name if msg.event else "Unknown Event",
            sender_id=msg.sender_id,
            sender_name=msg.sender_name,
            sender_email=msg.sender_email,
            sender_phone=msg.sender_phone,
            is_enrolled=msg.is_enrolled,
            subject=msg.subject,
            message=msg.message,
            is_read=msg.is_read,
            read_at=msg.read_at,
            created_at=msg.created_at,
        )
        for msg in messages
    ]

    return OrganizerMessageListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.patch("/organizer/messages/{message_id}/read")
async def mark_message_read(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """Mark a message as read."""
    # Get the message with event info
    query = (
        select(OrganizerMessage)
        .options(selectinload(OrganizerMessage.event))
        .where(OrganizerMessage.id == message_id)
    )
    result = await db.execute(query)
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Check if user owns the event
    if message.event.created_by_id != current_user.id:
        # Also allow admins
        user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
        if "administrator" not in user_roles:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to access this message",
            )

    # Mark as read
    if not message.is_read:
        message.is_read = True
        message.read_at = datetime.now(timezone.utc)
        await db.commit()

    return {"success": True, "message": "Message marked as read"}


@router.get("/organizer/messages/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """Get count of unread messages for the current organizer."""
    # Check if user is an organizer
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_organizer = "organizer" in user_roles
    is_admin = "administrator" in user_roles

    if not is_organizer and not is_admin:
        return UnreadCountResponse(unread_count=0)

    # Get events created by this user
    events_query = select(Event.id).where(
        Event.created_by_id == current_user.id,
        Event.is_deleted == False,
    )
    events_result = await db.execute(events_query)
    event_ids = [row[0] for row in events_result.all()]

    if not event_ids:
        return UnreadCountResponse(unread_count=0)

    # Count unread messages
    count_query = select(func.count(OrganizerMessage.id)).where(
        OrganizerMessage.event_id.in_(event_ids),
        OrganizerMessage.is_read == False,
    )
    result = await db.execute(count_query)
    unread_count = result.scalar() or 0

    return UnreadCountResponse(unread_count=unread_count)
