"""Notification endpoints."""

import asyncio
import json
from datetime import datetime, timezone
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount
from app.models.notification import Notification
from app.schemas.notification import (
    NotificationResponse,
    NotificationListResponse,
    NotificationStats,
)
from app.schemas.common import MessageResponse

router = APIRouter()


# ============================================================================
# SSE (Server-Sent Events) for Real-Time Notifications
# ============================================================================


@router.get("/stream")
async def notification_stream(
    request: Request,
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Server-Sent Events stream for real-time notifications.

    The client connects to this endpoint and receives:
    - Heartbeat events every 15 seconds to keep connection alive
    - Stats events every 5 seconds with current unread count
    - New notification events when they are created

    Event format:
    - event: heartbeat
      data: {"timestamp": "..."}

    - event: stats
      data: {"total": N, "unread": N}

    - event: notification
      data: {notification object}
    """
    async def event_generator():
        last_check_id = 0

        # Get initial latest notification ID
        from app.database import async_session_factory
        async with async_session_factory() as db:
            latest_query = select(func.max(Notification.id)).where(
                Notification.user_id == current_user.id
            )
            result = await db.execute(latest_query)
            last_check_id = result.scalar() or 0

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                async with async_session_factory() as db:
                    # Check for new notifications since last check
                    new_notifs_query = (
                        select(Notification)
                        .where(
                            Notification.user_id == current_user.id,
                            Notification.id > last_check_id,
                        )
                        .order_by(Notification.id)
                    )
                    result = await db.execute(new_notifs_query)
                    new_notifications = result.scalars().all()

                    # Send new notification events
                    for notif in new_notifications:
                        notif_data = {
                            "id": notif.id,
                            "user_id": notif.user_id,
                            "type": notif.type,
                            "title": notif.title,
                            "message": notif.message,
                            "data": notif.data,
                            "is_read": notif.is_read,
                            "created_at": notif.created_at.isoformat() if notif.created_at else None,
                        }
                        yield f"event: notification\ndata: {json.dumps(notif_data)}\n\n"
                        last_check_id = max(last_check_id, notif.id)

                    # Send stats update
                    unread_query = select(func.count(Notification.id)).where(
                        Notification.user_id == current_user.id,
                        Notification.is_read == False,
                    )
                    unread_result = await db.execute(unread_query)
                    unread = unread_result.scalar()

                    total_query = select(func.count(Notification.id)).where(
                        Notification.user_id == current_user.id
                    )
                    total_result = await db.execute(total_query)
                    total = total_result.scalar()

                    stats_data = {"total": total, "unread": unread}
                    yield f"event: stats\ndata: {json.dumps(stats_data)}\n\n"

            except Exception as e:
                # Log error but keep connection alive
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

            # Wait 5 seconds before next check
            await asyncio.sleep(5)

            # Send heartbeat
            heartbeat_data = {"timestamp": datetime.now(timezone.utc).isoformat()}
            yield f"event: heartbeat\ndata: {json.dumps(heartbeat_data)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    unread_only: bool = Query(False),
    notification_type: str | None = Query(None, alias="type"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    List notifications for current user.
    """
    # Build query
    query = select(Notification).where(Notification.user_id == current_user.id)

    if unread_only:
        query = query.where(Notification.is_read == False)

    if notification_type:
        query = query.where(Notification.type == notification_type)

    # Get total count
    count_query = select(func.count(Notification.id)).where(
        Notification.user_id == current_user.id
    )
    if unread_only:
        count_query = count_query.where(Notification.is_read == False)
    if notification_type:
        count_query = count_query.where(Notification.type == notification_type)
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Get unread count
    unread_count_query = select(func.count(Notification.id)).where(
        Notification.user_id == current_user.id,
        Notification.is_read == False,
    )
    unread_result = await db.execute(unread_count_query)
    unread_count = unread_result.scalar()

    # Pagination
    offset = (page - 1) * page_size
    query = query.order_by(Notification.created_at.desc()).offset(offset).limit(page_size)

    result = await db.execute(query)
    notifications = result.scalars().all()

    return NotificationListResponse(
        items=[NotificationResponse.model_validate(n) for n in notifications],
        total=total,
        unread_count=unread_count,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.get("/stats", response_model=NotificationStats)
async def get_notification_stats(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get notification statistics for current user.
    """
    # Total count
    total_query = select(func.count(Notification.id)).where(
        Notification.user_id == current_user.id
    )
    total_result = await db.execute(total_query)
    total = total_result.scalar()

    # Unread count
    unread_query = select(func.count(Notification.id)).where(
        Notification.user_id == current_user.id,
        Notification.is_read == False,
    )
    unread_result = await db.execute(unread_query)
    unread = unread_result.scalar()

    return NotificationStats(total=total, unread=unread)


@router.get("/{notification_id}", response_model=NotificationResponse)
async def get_notification(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get a specific notification.
    """
    query = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == current_user.id,
    )
    result = await db.execute(query)
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    return NotificationResponse.model_validate(notification)


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
async def mark_as_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Mark a notification as read.
    """
    query = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == current_user.id,
    )
    result = await db.execute(query)
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(notification)

    return NotificationResponse.model_validate(notification)


@router.post("/read-all", response_model=MessageResponse)
async def mark_all_as_read(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Mark all notifications as read for current user.
    """
    stmt = (
        update(Notification)
        .where(
            Notification.user_id == current_user.id,
            Notification.is_read == False,
        )
        .values(
            is_read=True,
            read_at=datetime.now(timezone.utc),
        )
    )
    await db.execute(stmt)
    await db.commit()

    return {"message": "All notifications marked as read"}


@router.delete("/{notification_id}", response_model=MessageResponse)
async def delete_notification(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Delete a notification.
    """
    query = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == current_user.id,
    )
    result = await db.execute(query)
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    await db.delete(notification)
    await db.commit()

    return {"message": "Notification deleted"}


@router.delete("", response_model=MessageResponse)
async def delete_all_read_notifications(
    all: bool = Query(False, description="Delete all notifications, not just read ones"),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Delete notifications for current user.

    By default deletes only read notifications.
    Pass ?all=true to delete all notifications (read + unread).
    """
    query = select(Notification).where(
        Notification.user_id == current_user.id,
    )
    if not all:
        query = query.where(Notification.is_read == True)

    result = await db.execute(query)
    notifications = result.scalars().all()

    for notification in notifications:
        await db.delete(notification)

    await db.commit()

    label = "all" if all else "read"
    return {"message": f"Deleted {len(notifications)} {label} notifications"}


# ============================================================================
# Notification Service Helper (for internal use)
# ============================================================================


async def create_notification(
    db: AsyncSession,
    user_id: int,
    notification_type: str,
    title: str,
    message: str,
    data: dict | None = None,
) -> Notification:
    """
    Create a new notification (internal helper).

    Common notification types:
    - event_enrollment_approved
    - event_enrollment_rejected
    - catch_validated
    - catch_rejected
    - club_invitation
    - event_starting_soon
    - ranking_change
    """
    notification = Notification(
        user_id=user_id,
        type=notification_type,
        title=title,
        message=message,
        data=data,
    )
    db.add(notification)
    await db.flush()
    return notification
