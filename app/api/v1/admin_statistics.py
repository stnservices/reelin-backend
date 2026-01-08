"""Admin statistics endpoints for manual recalculation."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import select, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import UserAccount
from app.models.event import Event
from app.models.enrollment import EventEnrollment
from app.models.trout_area import TALineup
from app.core.permissions import AdminOnly
from app.services.statistics_service import statistics_service


router = APIRouter()


class StatsRecalculateResponse(BaseModel):
    """Response for stats recalculation."""
    success: bool
    message: str
    user_id: Optional[int] = None
    users_processed: Optional[int] = None


@router.post("/users/{user_id}/recalculate", response_model=StatsRecalculateResponse)
async def recalculate_user_stats(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> StatsRecalculateResponse:
    """
    Manually recalculate all statistics for a specific user.

    Admin only endpoint. Use this to fix data inconsistencies or
    after bulk data imports/corrections.

    Recalculates:
    - Overall stats (across all event types)
    - Per event type stats (SF, TA)
    - TA-specific stats (matches, wins, catches)
    """
    # Verify user exists
    user = await db.get(UserAccount, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Recalculate all stats
    await statistics_service.recalculate_all_stats(db, user_id)
    await db.commit()

    return StatsRecalculateResponse(
        success=True,
        message=f"Statistics recalculated for user {user_id}",
        user_id=user_id,
    )


@router.post("/events/{event_id}/recalculate", response_model=StatsRecalculateResponse)
async def recalculate_event_stats(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> StatsRecalculateResponse:
    """
    Manually recalculate statistics for all participants in an event.

    Admin only endpoint. Use this after event completion issues or
    when stats weren't properly calculated.

    Includes participants from:
    - Regular enrollments
    - TA lineups (non-ghost)
    """
    # Verify event exists
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Collect all participant user IDs
    user_ids = set()

    # From enrollments
    enrollment_stmt = (
        select(distinct(EventEnrollment.user_id))
        .where(EventEnrollment.event_id == event_id)
        .where(EventEnrollment.status == "approved")
    )
    result = await db.execute(enrollment_stmt)
    user_ids.update(row[0] for row in result.fetchall())

    # From TA lineups (non-ghost participants)
    ta_lineup_stmt = (
        select(distinct(TALineup.user_id))
        .where(TALineup.event_id == event_id)
        .where(TALineup.is_ghost == False)
        .where(TALineup.user_id.isnot(None))
    )
    result = await db.execute(ta_lineup_stmt)
    user_ids.update(row[0] for row in result.fetchall() if row[0])

    if not user_ids:
        raise HTTPException(
            status_code=400,
            detail="No participants found for this event"
        )

    # Recalculate stats for each participant
    for user_id in user_ids:
        await statistics_service.recalculate_all_stats(db, user_id)

    await db.commit()

    return StatsRecalculateResponse(
        success=True,
        message=f"Statistics recalculated for {len(user_ids)} participants in event {event_id}",
        users_processed=len(user_ids),
    )


@router.post("/recalculate-all", response_model=StatsRecalculateResponse)
async def recalculate_all_stats(
    background_tasks: BackgroundTasks,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> StatsRecalculateResponse:
    """
    Recalculate statistics for all users (paginated).

    Admin only endpoint. Use with caution - this can be slow
    for large user bases.

    Parameters:
    - limit: Max users to process (default 100, max 500)
    - offset: Starting offset for pagination

    Call multiple times with increasing offset to process all users.
    """
    if limit > 500:
        limit = 500

    # Get batch of users with any event participation
    stmt = (
        select(distinct(EventEnrollment.user_id))
        .where(EventEnrollment.status == "approved")
        .order_by(EventEnrollment.user_id)
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    user_ids = [row[0] for row in result.fetchall()]

    if not user_ids:
        return StatsRecalculateResponse(
            success=True,
            message="No more users to process",
            users_processed=0,
        )

    # Recalculate stats for each user
    for user_id in user_ids:
        await statistics_service.recalculate_all_stats(db, user_id)

    await db.commit()

    return StatsRecalculateResponse(
        success=True,
        message=f"Statistics recalculated for {len(user_ids)} users (offset={offset})",
        users_processed=len(user_ids),
    )
