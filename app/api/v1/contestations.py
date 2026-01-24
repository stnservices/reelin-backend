"""Event contestations endpoints for disputes and reports."""

from datetime import datetime, timezone, timedelta
from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.core.permissions import OrganizerOrAdmin
from app.models.user import UserAccount
from app.models.event import Event, EventStatus
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.catch import Catch, EventScoreboard
from app.models.contestation import EventContestation, ContestationStatus, ContestationType
from app.schemas.contestation import (
    ContestationCreate,
    ContestationUpdate,
    ContestationReview,
    ContestationResponse,
    ContestationDetailResponse,
    ContestationListResponse,
    ContestationDetailListResponse,
)
from app.schemas.common import MessageResponse

router = APIRouter()

# Time window for contestation submission after event ends (in hours)
CONTESTATION_WINDOW_HOURS = 1


def is_within_contestation_window(event: Event) -> bool:
    """
    Check if we're within the allowed time window for submitting contestations.
    Returns True if:
    - Event is ongoing (always allowed)
    - Event is completed AND within CONTESTATION_WINDOW_HOURS of end_date
    """
    if event.status == EventStatus.ONGOING.value:
        return True

    if event.status == EventStatus.COMPLETED.value:
        if not event.end_date:
            return True
        now = datetime.now(timezone.utc)
        end_time = event.end_date
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        deadline = end_time + timedelta(hours=CONTESTATION_WINDOW_HOURS)
        return now <= deadline

    return False


def get_contestation_deadline(event: Event) -> datetime | None:
    """Get the deadline for contestation submission."""
    if event.end_date:
        end_time = event.end_date
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        return end_time + timedelta(hours=CONTESTATION_WINDOW_HOURS)
    return None


async def is_approved_participant(db: AsyncSession, event_id: int, user_id: int) -> bool:
    """Check if user is an approved participant in the event."""
    query = select(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.user_id == user_id,
        EventEnrollment.status == EnrollmentStatus.APPROVED.value,
    )
    result = await db.execute(query)
    return result.scalar_one_or_none() is not None


async def is_organizer_or_admin(event: Event, user: UserAccount) -> bool:
    """Check if user is organizer of the event or an admin."""
    user_roles = set(user.profile.roles or []) if user.profile else set()
    is_admin = "administrator" in user_roles
    is_event_owner = event.created_by_id == user.id
    return is_admin or is_event_owner


async def recalculate_rankings(db: AsyncSession, event_id: int) -> None:
    """
    Recalculate rankings for all participants after penalty points change.
    Rankings are ordered by: total_points (desc), total_catches (desc), first_catch_time (asc)
    Uses batch update for efficiency.
    """
    # Batch update all ranks in a single SQL statement using window function
    # Also sets previous_rank to the old rank value
    await db.execute(
        text("""
            UPDATE event_scoreboards es
            SET previous_rank = es.rank,
                rank = ranked.new_rank,
                updated_at = now()
            FROM (
                SELECT id, ROW_NUMBER() OVER (
                    ORDER BY total_points DESC,
                             total_catches DESC,
                             species_count DESC,
                             best_catch_length DESC NULLS LAST,
                             average_length DESC,
                             first_catch_time ASC NULLS LAST
                ) as new_rank
                FROM event_scoreboards
                WHERE event_id = :event_id
            ) ranked
            WHERE es.id = ranked.id AND es.event_id = :event_id
        """),
        {"event_id": event_id}
    )


@router.get("/{event_id}/contestations", response_model=ContestationListResponse | ContestationDetailListResponse)
async def list_contestations(
    event_id: int,
    status_filter: Optional[ContestationStatus] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    List contestations for an event.

    - Approved participants see anonymous list (no reporter info)
    - Organizers/admins see full details including reporter identity
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check if user is organizer/admin or approved participant
    is_org_admin = await is_organizer_or_admin(event, current_user)
    is_participant = await is_approved_participant(db, event_id, current_user.id)

    if not is_org_admin and not is_participant:
        raise HTTPException(
            status_code=403,
            detail="Only approved participants and organizers can view contestations",
        )

    # Build query
    query = (
        select(EventContestation)
        .options(
            selectinload(EventContestation.reporter).selectinload(UserAccount.profile),
            selectinload(EventContestation.reported_user).selectinload(UserAccount.profile),
            selectinload(EventContestation.reviewed_by),
        )
        .where(EventContestation.event_id == event_id)
    )

    if status_filter:
        query = query.where(EventContestation.status == status_filter.value)

    # Get total count
    count_query = select(func.count(EventContestation.id)).where(
        EventContestation.event_id == event_id
    )
    if status_filter:
        count_query = count_query.where(EventContestation.status == status_filter.value)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Pagination
    offset = (page - 1) * page_size
    query = query.order_by(EventContestation.created_at.desc()).offset(offset).limit(page_size)

    result = await db.execute(query)
    contestations = result.scalars().all()

    pages = ceil(total / page_size) if total > 0 else 1

    # Return different response based on user role
    if is_org_admin:
        return ContestationDetailListResponse(
            items=[ContestationDetailResponse.from_contestation(c) for c in contestations],
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )
    else:
        return ContestationListResponse(
            items=[ContestationResponse.from_contestation(c) for c in contestations],
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )


@router.post("/{event_id}/contestations", response_model=ContestationResponse, status_code=status.HTTP_201_CREATED)
async def create_contestation(
    event_id: int,
    contestation_data: ContestationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Submit a new contestation/report for an event.

    Only approved participants can submit contestations.
    Submissions allowed during the event and up to 1 hour after it ends.
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check if user is an approved participant
    if not await is_approved_participant(db, event_id, current_user.id):
        raise HTTPException(
            status_code=403,
            detail="Only approved participants can submit contestations",
        )

    # Check time window
    if not is_within_contestation_window(event):
        deadline = get_contestation_deadline(event)
        raise HTTPException(
            status_code=400,
            detail=f"Contestation submission window has expired. "
            f"Submissions are only allowed during the event and up to {CONTESTATION_WINDOW_HOURS} hour(s) after it ends."
            + (f" Deadline was: {deadline.isoformat()}" if deadline else ""),
        )

    # Validate reported_user_id if provided
    if contestation_data.reported_user_id:
        # Check if reported user is a participant
        if not await is_approved_participant(db, event_id, contestation_data.reported_user_id):
            raise HTTPException(
                status_code=400,
                detail="Reported user is not an approved participant in this event",
            )
        # Can't report yourself
        if contestation_data.reported_user_id == current_user.id:
            raise HTTPException(
                status_code=400,
                detail="You cannot submit a contestation against yourself",
            )

    # Validate reported_catch_id if provided
    if contestation_data.reported_catch_id:
        catch_query = select(Catch).where(
            Catch.id == contestation_data.reported_catch_id,
            Catch.event_id == event_id,
        )
        catch_result = await db.execute(catch_query)
        catch = catch_result.scalar_one_or_none()
        if not catch:
            raise HTTPException(
                status_code=400,
                detail="Reported catch not found in this event",
            )

    # Create contestation
    contestation = EventContestation(
        event_id=event_id,
        reporter_user_id=current_user.id,
        reported_user_id=contestation_data.reported_user_id,
        reported_catch_id=contestation_data.reported_catch_id,
        contestation_type=contestation_data.contestation_type.value,
        title=contestation_data.title,
        description=contestation_data.description,
        evidence_url=contestation_data.evidence_url,
        status=ContestationStatus.PENDING.value,
    )

    db.add(contestation)
    await db.commit()
    await db.refresh(contestation)

    # Reload with relationships
    query = (
        select(EventContestation)
        .options(
            selectinload(EventContestation.reported_user).selectinload(UserAccount.profile),
        )
        .where(EventContestation.id == contestation.id)
    )
    result = await db.execute(query)
    contestation = result.scalar_one()

    return ContestationResponse.from_contestation(contestation)


@router.get("/{event_id}/contestations/{contestation_id}")
async def get_contestation(
    event_id: int,
    contestation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get a single contestation.

    - Reporter sees their own contestation with full details
    - Approved participants see anonymous version
    - Organizers/admins see full details
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get contestation
    query = (
        select(EventContestation)
        .options(
            selectinload(EventContestation.reporter).selectinload(UserAccount.profile),
            selectinload(EventContestation.reported_user).selectinload(UserAccount.profile),
            selectinload(EventContestation.reviewed_by),
        )
        .where(
            EventContestation.id == contestation_id,
            EventContestation.event_id == event_id,
        )
    )
    result = await db.execute(query)
    contestation = result.scalar_one_or_none()

    if not contestation:
        raise HTTPException(status_code=404, detail="Contestation not found")

    # Check permissions
    is_org_admin = await is_organizer_or_admin(event, current_user)
    is_participant = await is_approved_participant(db, event_id, current_user.id)
    is_reporter = contestation.reporter_user_id == current_user.id

    if not is_org_admin and not is_participant:
        raise HTTPException(
            status_code=403,
            detail="Only approved participants and organizers can view contestations",
        )

    # Return detailed view for organizers and reporters, anonymous for others
    if is_org_admin or is_reporter:
        return ContestationDetailResponse.from_contestation(contestation)
    else:
        return ContestationResponse.from_contestation(contestation)


@router.patch("/{event_id}/contestations/{contestation_id}", response_model=ContestationResponse)
async def update_contestation(
    event_id: int,
    contestation_id: int,
    update_data: ContestationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Update a pending contestation.

    Only the original reporter can edit their own pending contestations.
    Must be within the contestation submission time window.
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get contestation
    query = (
        select(EventContestation)
        .options(
            selectinload(EventContestation.reported_user).selectinload(UserAccount.profile),
        )
        .where(
            EventContestation.id == contestation_id,
            EventContestation.event_id == event_id,
        )
    )
    result = await db.execute(query)
    contestation = result.scalar_one_or_none()

    if not contestation:
        raise HTTPException(status_code=404, detail="Contestation not found")

    # Check if user is the reporter
    if contestation.reporter_user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the original reporter can edit a contestation",
        )

    # Check if contestation is still pending
    if contestation.status != ContestationStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail="Can only edit pending contestations",
        )

    # Check time window
    if not is_within_contestation_window(event):
        deadline = get_contestation_deadline(event)
        raise HTTPException(
            status_code=400,
            detail=f"Contestation edit window has expired. "
            f"Edits are only allowed during the event and up to {CONTESTATION_WINDOW_HOURS} hour(s) after it ends."
            + (f" Deadline was: {deadline.isoformat()}" if deadline else ""),
        )

    # Apply updates
    update_fields = update_data.model_dump(exclude_unset=True)
    for field, value in update_fields.items():
        setattr(contestation, field, value)

    await db.commit()
    await db.refresh(contestation)

    return ContestationResponse.from_contestation(contestation)


@router.post("/{event_id}/contestations/{contestation_id}/review", response_model=ContestationDetailResponse)
async def review_contestation(
    event_id: int,
    contestation_id: int,
    review_data: ContestationReview,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
):
    """
    Review a contestation (approve or reject).

    If approved with penalty_points > 0, applies penalty to reported user's scoreboard.
    Only organizers and admins can review contestations.
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check if user has permission for this event
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_admin = "administrator" in user_roles
    is_event_owner = event.created_by_id == current_user.id

    if not is_admin and not is_event_owner:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to review contestations for this event",
        )

    # Get contestation
    query = (
        select(EventContestation)
        .options(
            selectinload(EventContestation.reporter).selectinload(UserAccount.profile),
            selectinload(EventContestation.reported_user).selectinload(UserAccount.profile),
        )
        .where(
            EventContestation.id == contestation_id,
            EventContestation.event_id == event_id,
        )
    )
    result = await db.execute(query)
    contestation = result.scalar_one_or_none()

    if not contestation:
        raise HTTPException(status_code=404, detail="Contestation not found")

    # Check if already reviewed
    if contestation.status != ContestationStatus.PENDING.value:
        raise HTTPException(
            status_code=400,
            detail=f"Contestation has already been reviewed (status: {contestation.status})",
        )

    # Apply penalty points if approved and penalty > 0
    if review_data.status == "approved" and review_data.penalty_points > 0:
        if not contestation.reported_user_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot apply penalty points: no user was reported in this contestation",
            )

        # Find reported user's scoreboard
        scoreboard_query = select(EventScoreboard).where(
            EventScoreboard.event_id == event_id,
            EventScoreboard.user_id == contestation.reported_user_id,
        )
        scoreboard_result = await db.execute(scoreboard_query)
        scoreboard = scoreboard_result.scalar_one_or_none()

        if scoreboard:
            # Apply penalty points
            scoreboard.penalty_points += review_data.penalty_points
            # Recalculate total points (deduct penalty)
            scoreboard.total_points = max(0, scoreboard.total_points - review_data.penalty_points)

    # Update contestation
    contestation.status = review_data.status
    contestation.reviewed_by_id = current_user.id
    contestation.reviewed_at = datetime.now(timezone.utc)
    contestation.review_notes = review_data.review_notes
    contestation.penalty_points_applied = review_data.penalty_points if review_data.status == "approved" else 0

    await db.flush()

    # Recalculate rankings if penalty was applied
    if review_data.status == "approved" and review_data.penalty_points > 0:
        await recalculate_rankings(db, event_id)

    await db.commit()

    # Reload with relationships
    query = (
        select(EventContestation)
        .options(
            selectinload(EventContestation.reporter).selectinload(UserAccount.profile),
            selectinload(EventContestation.reported_user).selectinload(UserAccount.profile),
            selectinload(EventContestation.reviewed_by),
        )
        .where(EventContestation.id == contestation_id)
    )
    result = await db.execute(query)
    contestation = result.scalar_one()

    return ContestationDetailResponse.from_contestation(contestation)


@router.delete("/{event_id}/contestations/{contestation_id}", response_model=MessageResponse)
async def delete_contestation(
    event_id: int,
    contestation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Delete a contestation.

    - Admins can delete any contestation (for spam removal)
    - Reporters can delete their own pending contestations
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get contestation
    query = select(EventContestation).where(
        EventContestation.id == contestation_id,
        EventContestation.event_id == event_id,
    )
    result = await db.execute(query)
    contestation = result.scalar_one_or_none()

    if not contestation:
        raise HTTPException(status_code=404, detail="Contestation not found")

    # Check permissions
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_admin = "administrator" in user_roles
    is_reporter = contestation.reporter_user_id == current_user.id

    if is_admin:
        # Admins can delete any contestation
        await db.delete(contestation)
        await db.commit()
        return {"message": "Contestation deleted successfully"}
    elif is_reporter:
        # Reporters can only delete their own pending contestations
        if contestation.status != ContestationStatus.PENDING.value:
            raise HTTPException(
                status_code=400,
                detail="Can only delete pending contestations",
            )
        await db.delete(contestation)
        await db.commit()
        return {"message": "Contestation deleted successfully"}
    else:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to delete this contestation",
        )


@router.get("/{event_id}/contestations/my", response_model=ContestationListResponse)
async def list_my_contestations(
    event_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    List contestations submitted by the current user for an event.
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Build query
    query = (
        select(EventContestation)
        .options(
            selectinload(EventContestation.reported_user).selectinload(UserAccount.profile),
            selectinload(EventContestation.reviewed_by),
        )
        .where(
            EventContestation.event_id == event_id,
            EventContestation.reporter_user_id == current_user.id,
        )
    )

    # Get total count
    count_query = select(func.count(EventContestation.id)).where(
        EventContestation.event_id == event_id,
        EventContestation.reporter_user_id == current_user.id,
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Pagination
    offset = (page - 1) * page_size
    query = query.order_by(EventContestation.created_at.desc()).offset(offset).limit(page_size)

    result = await db.execute(query)
    contestations = result.scalars().all()

    pages = ceil(total / page_size) if total > 0 else 1

    return ContestationListResponse(
        items=[ContestationResponse.from_contestation(c) for c in contestations],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )
