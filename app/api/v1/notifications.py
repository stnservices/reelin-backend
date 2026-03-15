"""Notification endpoints."""

from datetime import datetime, timezone
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_id_cached
from app.models.notification import Notification
from app.schemas.notification import (
    NotificationResponse,
    NotificationListResponse,
    NotificationStats,
)
from app.schemas.common import MessageResponse

router = APIRouter()


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    unread_only: bool = Query(False),
    notification_type: str | None = Query(None, alias="type"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    List notifications for current user.
    """
    # Build query
    query = select(Notification).where(Notification.user_id == user_id)

    if unread_only:
        query = query.where(Notification.is_read == False)

    if notification_type:
        query = query.where(Notification.type == notification_type)

    # Get total count
    count_query = select(func.count(Notification.id)).where(
        Notification.user_id == user_id
    )
    if unread_only:
        count_query = count_query.where(Notification.is_read == False)
    if notification_type:
        count_query = count_query.where(Notification.type == notification_type)
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Get unread count
    unread_count_query = select(func.count(Notification.id)).where(
        Notification.user_id == user_id,
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
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Get notification statistics for current user.
    """
    # Total count
    total_query = select(func.count(Notification.id)).where(
        Notification.user_id == user_id
    )
    total_result = await db.execute(total_query)
    total = total_result.scalar()

    # Unread count
    unread_query = select(func.count(Notification.id)).where(
        Notification.user_id == user_id,
        Notification.is_read == False,
    )
    unread_result = await db.execute(unread_query)
    unread = unread_result.scalar()

    return NotificationStats(total=total, unread=unread)


@router.get("/{notification_id}", response_model=NotificationResponse)
async def get_notification(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Get a specific notification.
    """
    query = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == user_id,
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
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Mark a notification as read.
    """
    query = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == user_id,
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
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Mark all notifications as read for current user.
    """
    stmt = (
        update(Notification)
        .where(
            Notification.user_id == user_id,
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
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Delete a notification.
    """
    query = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == user_id,
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
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Delete notifications for current user.

    By default deletes only read notifications.
    Pass ?all=true to delete all notifications (read + unread).
    """
    query = select(Notification).where(
        Notification.user_id == user_id,
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
