"""Admin message endpoints for platform contact form."""

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
from app.models.admin_message import AdminMessage
from app.schemas.admin_message import (
    AdminMessageCreate,
    AdminMessageResponse,
    AdminMessageListResponse,
    AdminMessageSendResponse,
    AdminUnreadCountResponse,
)
from app.services.email import EmailService

router = APIRouter()

# Rate limit: 1 message per day per user
RATE_LIMIT_HOURS = 24


async def check_rate_limit(db: AsyncSession, sender_id: int) -> bool:
    """
    Check if user can send a message (rate limit: 1 per day).
    Returns True if allowed, False if rate limited.
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=RATE_LIMIT_HOURS)

    query = select(AdminMessage).where(
        AdminMessage.sender_id == sender_id,
        AdminMessage.created_at >= cutoff_time,
    )
    result = await db.execute(query)
    existing = result.scalar_one_or_none()

    return existing is None


def send_admin_notification(
    sender_name: str,
    sender_email: str,
    sender_phone: Optional[str],
    subject: str,
    message: str,
) -> bool:
    """Send email notification to admins about new contact message."""
    try:
        email_service = EmailService()
        if not email_service.is_configured():
            return False

        # Admin notification email
        admin_email = "contact@reelin.ro"

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>New Contact Message</title>
</head>
<body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; margin: 0; padding: 0; background-color: #f4f4f4;">
    <table cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #f4f4f4;">
        <tr>
            <td style="padding: 20px;">
                <table cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <!-- Header -->
                    <tr>
                        <td style="padding: 40px 30px; text-align: center; background-color: #ffc107; border-radius: 8px 8px 0 0;">
                            <img src="https://streetfishing.fra1.cdn.digitaloceanspaces.com/media/assets/logo_orange_yellow_transparent.png" alt="ReelIn Logo" width="120" style="margin-bottom: 10px;">
                            <h1 style="color: #000000; font-size: 24px; margin: 0;">
                                New Contact Us Message
                            </h1>
                        </td>
                    </tr>
                    <!-- Content -->
                    <tr>
                        <td style="padding: 30px;">
                            <p style="font-size: 16px; line-height: 1.5; color: #333333; margin-bottom: 20px;">
                                A user has submitted a message through the Contact Us form.
                            </p>

                            <!-- Sender Info Box -->
                            <table cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #f8f9fa; border-radius: 8px; margin: 20px 0;">
                                <tr>
                                    <td style="padding: 20px;">
                                        <p style="font-size: 14px; color: #666666; margin: 0 0 5px 0;"><strong>From:</strong> {sender_name}</p>
                                        <p style="font-size: 14px; color: #666666; margin: 0 0 5px 0;"><strong>Email:</strong> <a href="mailto:{sender_email}" style="color: #007bff;">{sender_email}</a></p>
                                        {f'<p style="font-size: 14px; color: #666666; margin: 0;"><strong>Phone:</strong> {sender_phone}</p>' if sender_phone else ''}
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
                                Reply directly to the user at <a href="mailto:{sender_email}" style="color: #007bff;">{sender_email}</a>.
                            </p>
                        </td>
                    </tr>
                    <!-- Footer -->
                    <tr>
                        <td style="padding: 20px 30px; background-color: #f8f9fa; border-radius: 0 0 8px 8px; text-align: center;">
                            <p style="font-size: 12px; color: #999999; margin: 0;">
                                View all messages in the Admin Dashboard.
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
            to_email=admin_email,
            subject=f"[Contact Us] {subject}",
            html_content=html_content,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to send admin notification: {e}")
        return False


@router.post(
    "/contact-admin",
    response_model=AdminMessageSendResponse,
    status_code=status.HTTP_201_CREATED,
)
async def contact_admin(
    message_data: AdminMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Send a message to platform administrators.

    - Any logged-in user can contact admins
    - Rate limited to 1 message per day per user
    - Sender's name, email, and phone are included with the message
    """
    # Check rate limit
    if not await check_rate_limit(db, current_user.id):
        raise HTTPException(
            status_code=429,
            detail="You can only send one message per day. Please try again later.",
        )

    # Get sender profile info
    sender_profile = current_user.profile
    if not sender_profile:
        raise HTTPException(status_code=400, detail="User profile not found")

    sender_name = sender_profile.full_name
    sender_email = current_user.email
    sender_phone = sender_profile.phone

    # Create message
    admin_message = AdminMessage(
        sender_id=current_user.id,
        subject=message_data.subject,
        message=message_data.message,
        sender_name=sender_name,
        sender_email=sender_email,
        sender_phone=sender_phone,
    )

    db.add(admin_message)
    await db.commit()

    # Send email notification to admins
    send_admin_notification(
        sender_name=sender_name,
        sender_email=sender_email,
        sender_phone=sender_phone,
        subject=message_data.subject,
        message=message_data.message,
    )

    return AdminMessageSendResponse(success=True, message="Message sent successfully")


@router.get("/admin/contact-messages", response_model=AdminMessageListResponse)
async def list_admin_messages(
    is_read: Optional[bool] = Query(None, description="Filter by read status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    List all contact messages sent to admins.

    Only administrators can access this endpoint.
    """
    # Check if user is an admin
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    if "administrator" not in user_roles:
        raise HTTPException(
            status_code=403,
            detail="Only administrators can access contact messages",
        )

    # Build query for messages
    query = select(AdminMessage).options(
        selectinload(AdminMessage.sender).selectinload(UserAccount.profile),
        selectinload(AdminMessage.read_by).selectinload(UserAccount.profile),
    )

    count_query = select(func.count(AdminMessage.id))

    # Apply filter
    if is_read is not None:
        query = query.where(AdminMessage.is_read == is_read)
        count_query = count_query.where(AdminMessage.is_read == is_read)

    # Count total
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Count unread
    unread_query = select(func.count(AdminMessage.id)).where(AdminMessage.is_read == False)
    unread_result = await db.execute(unread_query)
    unread_count = unread_result.scalar() or 0

    # Pagination
    offset = (page - 1) * page_size
    query = query.order_by(AdminMessage.created_at.desc()).offset(offset).limit(page_size)

    result = await db.execute(query)
    messages = result.scalars().all()

    pages = ceil(total / page_size) if total > 0 else 1

    # Build response items
    items = []
    for msg in messages:
        read_by_name = None
        if msg.read_by and msg.read_by.profile:
            read_by_name = msg.read_by.profile.full_name

        items.append(
            AdminMessageResponse(
                id=msg.id,
                sender_id=msg.sender_id,
                sender_name=msg.sender_name,
                sender_email=msg.sender_email,
                sender_phone=msg.sender_phone,
                subject=msg.subject,
                message=msg.message,
                is_read=msg.is_read,
                read_at=msg.read_at,
                read_by_name=read_by_name,
                created_at=msg.created_at,
            )
        )

    return AdminMessageListResponse(
        items=items,
        total=total,
        unread_count=unread_count,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.patch("/admin/contact-messages/{message_id}/read")
async def mark_admin_message_read(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """Mark a contact message as read."""
    # Check if user is an admin
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    if "administrator" not in user_roles:
        raise HTTPException(
            status_code=403,
            detail="Only administrators can access contact messages",
        )

    # Get the message
    query = select(AdminMessage).where(AdminMessage.id == message_id)
    result = await db.execute(query)
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Mark as read
    if not message.is_read:
        message.is_read = True
        message.read_at = datetime.now(timezone.utc)
        message.read_by_id = current_user.id
        await db.commit()

    return {"success": True, "message": "Message marked as read"}


@router.get("/admin/contact-messages/unread-count", response_model=AdminUnreadCountResponse)
async def get_admin_unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """Get count of unread contact messages for admins."""
    # Check if user is an admin
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    if "administrator" not in user_roles:
        return AdminUnreadCountResponse(unread_count=0)

    # Count unread messages
    count_query = select(func.count(AdminMessage.id)).where(
        AdminMessage.is_read == False,
    )
    result = await db.execute(count_query)
    unread_count = result.scalar() or 0

    return AdminUnreadCountResponse(unread_count=unread_count)
