"""Admin endpoints for content moderation management."""

from datetime import datetime
from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount
from app.models.profile_moderation import ProfilePictureModeration, ModerationStatus
from app.core.permissions import AdminOnly

router = APIRouter()


# ============== Schemas ==============


class ModerationLogResponse(BaseModel):
    """Schema for a single moderation log entry."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    image_url: str
    status: str
    adult_score: Optional[int] = None
    violence_score: Optional[int] = None
    racy_score: Optional[int] = None
    rejection_reason: Optional[str] = None
    processing_time_ms: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime
    processed_at: Optional[datetime] = None


class ModerationLogListResponse(BaseModel):
    """Paginated moderation log list response."""

    items: list[ModerationLogResponse]
    total: int
    page: int
    page_size: int
    pages: int


class ModerationStatsResponse(BaseModel):
    """Moderation statistics response."""

    total_checks: int
    approved_count: int
    rejected_count: int
    pending_count: int
    failed_count: int
    rejection_rate: float  # percentage
    unique_rejected_users: int


# ============== Endpoints ==============


@router.get("/logs", response_model=ModerationLogListResponse)
async def list_moderation_logs(
    status: Optional[str] = Query(None, description="Filter by status: pending, approved, rejected, failed"),
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    List all moderation logs with optional filters.
    Admin only.
    """
    # Build query
    query = (
        select(ProfilePictureModeration)
        .options(selectinload(ProfilePictureModeration.user))
        .order_by(desc(ProfilePictureModeration.created_at))
    )

    # Apply filters
    if status:
        query = query.where(ProfilePictureModeration.status == status)
    if user_id:
        query = query.where(ProfilePictureModeration.user_id == user_id)

    # Get total count
    count_query = select(func.count(ProfilePictureModeration.id))
    if status:
        count_query = count_query.where(ProfilePictureModeration.status == status)
    if user_id:
        count_query = count_query.where(ProfilePictureModeration.user_id == user_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    logs = result.scalars().all()

    # Build response
    items = []
    for log in logs:
        user_email = log.user.email if log.user else None
        user_name = None
        if log.user and log.user.profile:
            user_name = f"{log.user.profile.first_name} {log.user.profile.last_name}"

        items.append(
            ModerationLogResponse(
                id=log.id,
                user_id=log.user_id,
                user_email=user_email,
                user_name=user_name,
                image_url=log.image_url,
                status=log.status,
                adult_score=log.adult_score,
                violence_score=log.violence_score,
                racy_score=log.racy_score,
                rejection_reason=log.rejection_reason,
                processing_time_ms=log.processing_time_ms,
                error_message=log.error_message,
                created_at=log.created_at,
                processed_at=log.processed_at,
            )
        )

    return ModerationLogListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.get("/rejected", response_model=ModerationLogListResponse)
async def list_rejected_profile_pictures(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    List all rejected profile pictures.
    Admin only. Useful for reviewing flagged content.
    """
    # Build query for rejected only
    query = (
        select(ProfilePictureModeration)
        .options(selectinload(ProfilePictureModeration.user))
        .where(ProfilePictureModeration.status == ModerationStatus.REJECTED.value)
        .order_by(desc(ProfilePictureModeration.created_at))
    )

    # Get total count
    count_query = select(func.count(ProfilePictureModeration.id)).where(
        ProfilePictureModeration.status == ModerationStatus.REJECTED.value
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    logs = result.scalars().all()

    # Build response
    items = []
    for log in logs:
        user_email = log.user.email if log.user else None
        user_name = None
        if log.user and log.user.profile:
            user_name = f"{log.user.profile.first_name} {log.user.profile.last_name}"

        items.append(
            ModerationLogResponse(
                id=log.id,
                user_id=log.user_id,
                user_email=user_email,
                user_name=user_name,
                image_url=log.image_url,
                status=log.status,
                adult_score=log.adult_score,
                violence_score=log.violence_score,
                racy_score=log.racy_score,
                rejection_reason=log.rejection_reason,
                processing_time_ms=log.processing_time_ms,
                error_message=log.error_message,
                created_at=log.created_at,
                processed_at=log.processed_at,
            )
        )

    return ModerationLogListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.get("/user/{user_id}", response_model=ModerationLogListResponse)
async def get_user_moderation_history(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    Get moderation history for a specific user.
    Admin only. Shows all profile picture moderation attempts.
    """
    # Build query for specific user
    query = (
        select(ProfilePictureModeration)
        .options(selectinload(ProfilePictureModeration.user))
        .where(ProfilePictureModeration.user_id == user_id)
        .order_by(desc(ProfilePictureModeration.created_at))
    )

    # Get total count
    count_query = select(func.count(ProfilePictureModeration.id)).where(
        ProfilePictureModeration.user_id == user_id
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    logs = result.scalars().all()

    # Build response
    items = []
    for log in logs:
        user_email = log.user.email if log.user else None
        user_name = None
        if log.user and log.user.profile:
            user_name = f"{log.user.profile.first_name} {log.user.profile.last_name}"

        items.append(
            ModerationLogResponse(
                id=log.id,
                user_id=log.user_id,
                user_email=user_email,
                user_name=user_name,
                image_url=log.image_url,
                status=log.status,
                adult_score=log.adult_score,
                violence_score=log.violence_score,
                racy_score=log.racy_score,
                rejection_reason=log.rejection_reason,
                processing_time_ms=log.processing_time_ms,
                error_message=log.error_message,
                created_at=log.created_at,
                processed_at=log.processed_at,
            )
        )

    return ModerationLogListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.get("/stats", response_model=ModerationStatsResponse)
async def get_moderation_stats(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    Get overall moderation statistics.
    Admin only.
    """
    # Total checks
    total_query = select(func.count(ProfilePictureModeration.id))
    total_result = await db.execute(total_query)
    total_checks = total_result.scalar() or 0

    # Status counts
    status_query = select(
        ProfilePictureModeration.status,
        func.count(ProfilePictureModeration.id),
    ).group_by(ProfilePictureModeration.status)
    status_result = await db.execute(status_query)
    status_counts = dict(status_result.all())

    approved_count = status_counts.get(ModerationStatus.APPROVED.value, 0)
    rejected_count = status_counts.get(ModerationStatus.REJECTED.value, 0)
    pending_count = status_counts.get(ModerationStatus.PENDING.value, 0) + status_counts.get(
        ModerationStatus.PROCESSING.value, 0
    )
    failed_count = status_counts.get(ModerationStatus.FAILED.value, 0)

    # Unique rejected users
    unique_rejected_query = (
        select(func.count(func.distinct(ProfilePictureModeration.user_id)))
        .where(ProfilePictureModeration.status == ModerationStatus.REJECTED.value)
    )
    unique_result = await db.execute(unique_rejected_query)
    unique_rejected_users = unique_result.scalar() or 0

    # Rejection rate (exclude pending/failed)
    processed_count = approved_count + rejected_count
    rejection_rate = (rejected_count / processed_count * 100) if processed_count > 0 else 0.0

    return ModerationStatsResponse(
        total_checks=total_checks,
        approved_count=approved_count,
        rejected_count=rejected_count,
        pending_count=pending_count,
        failed_count=failed_count,
        rejection_rate=round(rejection_rate, 2),
        unique_rejected_users=unique_rejected_users,
    )
