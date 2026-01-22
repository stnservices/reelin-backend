"""Event reports and exports endpoints."""

import csv
import io
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, and_, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.core.permissions import OrganizerOrAdmin
from app.models.user import UserAccount
from app.models.event import Event, EventFishScoring, EventSpeciesBonusPoints, EventStatus
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.catch import Catch, CatchStatus, EventScoreboard
from app.models.fish import Fish

router = APIRouter()


def sanitize_filename(name: str) -> str:
    """Sanitize event name for use in filename."""
    # Replace spaces with underscores
    name = name.replace(" ", "_")
    # Remove special characters except underscores and hyphens
    name = re.sub(r"[^a-zA-Z0-9_\-]", "", name)
    # Truncate to reasonable length
    return name[:50]


@router.get("/{event_id}/public-stats")
async def get_event_public_stats(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> dict:
    """
    Get public statistics for a completed event.
    Available to any authenticated user but ONLY for completed events.

    Includes:
    - Total participants and catches
    - Species breakdown with counts and averages
    - Catches by hour distribution
    - Quality stats (above/below min length)
    - Averages (catches per participant, length per catch)
    - Top performers (most catches, biggest catch, most species)
    """
    # Verify event exists
    event_query = (
        select(Event)
        .options(
            selectinload(Event.event_type),
            selectinload(Event.scoring_config),
        )
        .where(Event.id == event_id)
    )
    result = await db.execute(event_query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    # Public stats only available for completed events
    if event.status != EventStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Statistics are only available for completed events",
        )

    # Get approved participant count
    participant_count_result = await db.execute(
        select(func.count(EventEnrollment.id)).where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
    )
    total_participants = participant_count_result.scalar() or 0

    # Get catch stats
    catch_stats = await db.execute(
        select(
            func.count().label("total"),
            func.avg(Catch.length).label("avg_length"),
            func.max(Catch.length).label("max_length"),
            func.sum(Catch.points).label("total_points"),
        ).where(
            Catch.event_id == event_id,
            Catch.status == CatchStatus.APPROVED.value,
        )
    )
    catch_row = catch_stats.one()
    total_catches = catch_row.total or 0
    avg_length = round(float(catch_row.avg_length), 2) if catch_row.avg_length else 0

    # Get unique species count
    species_count_result = await db.execute(
        select(func.count(func.distinct(Catch.fish_id))).where(
            Catch.event_id == event_id,
            Catch.status == CatchStatus.APPROVED.value,
        )
    )
    total_species = species_count_result.scalar() or 0

    # Get species breakdown
    species_stats = await db.execute(
        select(
            Fish.name.label("species"),
            func.count().label("count"),
            func.avg(Catch.length).label("avg_length"),
            func.max(Catch.length).label("max_length"),
        )
        .join(Fish, Catch.fish_id == Fish.id)
        .where(
            Catch.event_id == event_id,
            Catch.status == CatchStatus.APPROVED.value,
        )
        .group_by(Fish.id, Fish.name)
        .order_by(func.count().desc())
    )
    catches_by_species = [
        {
            "species": row.species,
            "count": row.count,
            "avg_length": round(float(row.avg_length), 2) if row.avg_length else 0,
            "max_length": float(row.max_length) if row.max_length else 0,
        }
        for row in species_stats.all()
    ]

    # Get catches by hour (using submitted_at or catch_time)
    # Extract hour from catch_time if available, otherwise submitted_at
    # Convert UTC to Romania timezone before extracting hour
    local_time = func.timezone('Europe/Bucharest', func.coalesce(Catch.catch_time, Catch.submitted_at))
    catches_by_hour_result = await db.execute(
        select(
            func.extract('hour', local_time).label("hour"),
            func.count().label("count"),
        )
        .where(
            Catch.event_id == event_id,
            Catch.status == CatchStatus.APPROVED.value,
        )
        .group_by(func.extract('hour', local_time))
        .order_by("hour")
    )
    catches_by_hour = [
        {
            "hour": f"{int(row.hour):02d}:00",
            "count": row.count,
        }
        for row in catches_by_hour_result.all()
    ]

    # Get quality stats using fish scoring config
    fish_scoring_query = (
        select(EventFishScoring)
        .where(EventFishScoring.event_id == event_id)
    )
    fish_scoring_result = await db.execute(fish_scoring_query)
    fish_scorings = {fs.fish_id: fs.accountable_min_length for fs in fish_scoring_result.scalars().all()}

    # Count above/below min length catches
    if fish_scorings:
        # Get all approved catches with their fish
        catches_query = await db.execute(
            select(Catch.fish_id, Catch.length).where(
                Catch.event_id == event_id,
                Catch.status == CatchStatus.APPROVED.value,
            )
        )
        all_catches = catches_query.all()

        above_min = 0
        below_min = 0
        for catch_fish_id, length in all_catches:
            min_length = fish_scorings.get(catch_fish_id)
            if min_length is not None:
                if length >= min_length:
                    above_min += 1
                else:
                    below_min += 1
            else:
                # No min length defined, count as above
                above_min += 1

        quality_stats = {
            "above_min_length": above_min,
            "below_min_length": below_min,
            "above_min_percentage": round((above_min / total_catches * 100), 1) if total_catches > 0 else 0,
        }
    else:
        quality_stats = {
            "above_min_length": total_catches,
            "below_min_length": 0,
            "above_min_percentage": 100.0 if total_catches > 0 else 0,
        }

    # Calculate averages
    averages = {
        "catches_per_participant": round(total_catches / total_participants, 2) if total_participants > 0 else 0,
        "length_per_catch": avg_length,
        "points_per_participant": round(float(catch_row.total_points or 0) / total_participants, 2) if total_participants > 0 else 0,
    }

    # Get top performers
    # Most catches
    most_catches_query = await db.execute(
        select(
            EventScoreboard.user_id,
            EventScoreboard.total_catches,
        )
        .where(EventScoreboard.event_id == event_id)
        .order_by(EventScoreboard.total_catches.desc())
        .limit(1)
    )
    most_catches_entry = most_catches_query.first()

    # We need to get user info separately
    most_catches = None
    if most_catches_entry and most_catches_entry.total_catches > 0:
        user_query = await db.execute(
            select(UserAccount).options(selectinload(UserAccount.profile)).where(
                UserAccount.id == most_catches_entry.user_id
            )
        )
        user = user_query.scalar_one_or_none()
        if user:
            profile = user.profile
            user_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip() if profile else user.email
            most_catches = {
                "user_name": user_name,
                "count": most_catches_entry.total_catches,
            }

    # Biggest catch - only accountable catches (length >= min_length for that species)
    # First, get event fish scoring config to know min_length per species
    fish_scoring_query = await db.execute(
        select(EventFishScoring).where(EventFishScoring.event_id == event_id)
    )
    fish_scoring_configs = {fs.fish_id: fs for fs in fish_scoring_query.scalars().all()}

    # Get all approved catches for this event
    all_catches_query = await db.execute(
        select(Catch)
        .options(
            selectinload(Catch.user).selectinload(UserAccount.profile),
            selectinload(Catch.fish),
        )
        .where(
            Catch.event_id == event_id,
            Catch.status == CatchStatus.APPROVED.value,
        )
        .order_by(Catch.length.desc())
    )
    all_catches = all_catches_query.scalars().all()

    # Filter for accountable catches (length >= min_length)
    accountable_catches = []
    non_accountable_catches = []
    for catch in all_catches:
        fish_config = fish_scoring_configs.get(catch.fish_id)
        min_length = fish_config.accountable_min_length if fish_config else 0
        if catch.length >= min_length:
            accountable_catches.append(catch)
        else:
            non_accountable_catches.append(catch)

    # Biggest catch is the largest accountable catch, or fallback to largest non-accountable
    biggest_catch_entry = None
    if accountable_catches:
        biggest_catch_entry = accountable_catches[0]  # Already sorted by length desc
    elif non_accountable_catches:
        biggest_catch_entry = non_accountable_catches[0]  # Fallback if no accountable catches

    biggest_catch = None
    if biggest_catch_entry:
        profile = biggest_catch_entry.user.profile if biggest_catch_entry.user else None
        user_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip() if profile else ""
        biggest_catch = {
            "user_name": user_name,
            "length": biggest_catch_entry.length,
            "species": biggest_catch_entry.fish.name if biggest_catch_entry.fish else "Unknown",
            "species_ro": biggest_catch_entry.fish.name_ro if biggest_catch_entry.fish else None,
            "is_accountable": len(accountable_catches) > 0 and biggest_catch_entry in accountable_catches,
        }

    # Most species
    most_species_query = await db.execute(
        select(
            EventScoreboard.user_id,
            EventScoreboard.species_count,
        )
        .where(EventScoreboard.event_id == event_id)
        .order_by(EventScoreboard.species_count.desc())
        .limit(1)
    )
    most_species_entry = most_species_query.first()

    most_species = None
    if most_species_entry and most_species_entry.species_count > 0:
        user_query = await db.execute(
            select(UserAccount).options(selectinload(UserAccount.profile)).where(
                UserAccount.id == most_species_entry.user_id
            )
        )
        user = user_query.scalar_one_or_none()
        if user:
            profile = user.profile
            user_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip() if profile else user.email
            most_species = {
                "user_name": user_name,
                "count": most_species_entry.species_count,
            }

    top_performers = {
        "most_catches": most_catches,
        "biggest_catch": biggest_catch,
        "most_species": most_species,
    }

    return {
        "event": {
            "id": event.id,
            "name": event.name,
            "status": event.status,
            "start_date": event.start_date.isoformat(),
            "end_date": event.end_date.isoformat(),
            "event_type": event.event_type.name,
        },
        "total_participants": total_participants,
        "total_catches": total_catches,
        "total_species_caught": total_species,
        "catches_by_species": catches_by_species,
        "catches_by_hour": catches_by_hour,
        "quality_stats": quality_stats,
        "averages": averages,
        "top_performers": top_performers,
    }


@router.get("/{event_id}/stats")
async def get_event_stats(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> dict:
    """
    Get comprehensive statistics for an event.
    Available to event organizer or admin.
    """
    # Verify event exists and user has access
    event_query = (
        select(Event)
        .options(
            selectinload(Event.event_type),
            selectinload(Event.scoring_config),
        )
        .where(Event.id == event_id)
    )
    result = await db.execute(event_query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    # Check access
    if event.created_by_id != current_user.id and not current_user.profile.has_any_role("administrator", "validator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this event's reports",
        )

    # Get enrollment stats
    enrollment_stats = await db.execute(
        select(
            func.count().label("total"),
            func.sum(case((EventEnrollment.status == EnrollmentStatus.APPROVED.value, 1), else_=0)).label("approved"),
            func.sum(case((EventEnrollment.status == EnrollmentStatus.PENDING.value, 1), else_=0)).label("pending"),
        ).where(EventEnrollment.event_id == event_id)
    )
    enrollment_row = enrollment_stats.one()

    # Get catch stats
    catch_stats = await db.execute(
        select(
            func.count().label("total"),
            func.sum(case((Catch.status == CatchStatus.APPROVED.value, 1), else_=0)).label("approved"),
            func.sum(case((Catch.status == CatchStatus.PENDING.value, 1), else_=0)).label("pending"),
            func.sum(case((Catch.status == CatchStatus.REJECTED.value, 1), else_=0)).label("rejected"),
            func.avg(case((Catch.status == CatchStatus.APPROVED.value, Catch.length), else_=None)).label("avg_length"),
            func.max(case((Catch.status == CatchStatus.APPROVED.value, Catch.length), else_=None)).label("max_length"),
            func.sum(case((Catch.status == CatchStatus.APPROVED.value, Catch.points), else_=0)).label("total_points"),
        ).where(Catch.event_id == event_id)
    )
    catch_row = catch_stats.one()

    # Get unique participants with catches
    unique_catchers = await db.execute(
        select(func.count(func.distinct(Catch.user_id)))
        .where(and_(Catch.event_id == event_id, Catch.status == CatchStatus.APPROVED.value))
    )
    unique_catcher_count = unique_catchers.scalar() or 0

    # Get species distribution
    species_stats = await db.execute(
        select(
            Fish.name,
            func.count().label("count"),
            func.avg(Catch.length).label("avg_length"),
            func.max(Catch.length).label("max_length"),
        )
        .join(Fish, Catch.fish_id == Fish.id)
        .where(and_(Catch.event_id == event_id, Catch.status == CatchStatus.APPROVED.value))
        .group_by(Fish.id, Fish.name)
        .order_by(func.count().desc())
    )
    species_data = [
        {
            "name": row.name,
            "count": row.count,
            "avg_length": round(float(row.avg_length), 2) if row.avg_length else 0,
            "max_length": float(row.max_length) if row.max_length else 0,
        }
        for row in species_stats.all()
    ]

    # Get fish scoring config
    fish_scoring_query = (
        select(EventFishScoring)
        .options(selectinload(EventFishScoring.fish))
        .where(EventFishScoring.event_id == event_id)
        .order_by(EventFishScoring.display_order)
    )
    fish_scoring_result = await db.execute(fish_scoring_query)
    fish_scoring = [
        {
            "fish_name": fs.fish.name if fs.fish else "Unknown",
            "catch_slots": fs.accountable_catch_slots,
            "min_length": fs.accountable_min_length,
            "under_min_points": fs.under_min_length_points,
        }
        for fs in fish_scoring_result.scalars().all()
    ]

    # Get bonus points config
    bonus_query = (
        select(EventSpeciesBonusPoints)
        .where(EventSpeciesBonusPoints.event_id == event_id)
        .order_by(EventSpeciesBonusPoints.species_count)
    )
    bonus_result = await db.execute(bonus_query)
    bonus_points = [
        {
            "species_count": bp.species_count,
            "bonus_points": bp.bonus_points,
        }
        for bp in bonus_result.scalars().all()
    ]

    return {
        "event": {
            "id": event.id,
            "name": event.name,
            "status": event.status,
            "start_date": event.start_date.isoformat(),
            "end_date": event.end_date.isoformat(),
            "event_type": event.event_type.name,
            "scoring_method": event.scoring_config.code,  # Use code as scoring method identifier
            "top_x_overall": event.top_x_overall,
            "has_bonus_points": event.has_bonus_points,
        },
        "enrollments": {
            "total": enrollment_row.total or 0,
            "approved": int(enrollment_row.approved or 0),
            "pending": int(enrollment_row.pending or 0),
        },
        "catches": {
            "total": catch_row.total or 0,
            "approved": int(catch_row.approved or 0),
            "pending": int(catch_row.pending or 0),
            "rejected": int(catch_row.rejected or 0),
            "avg_length": round(float(catch_row.avg_length), 2) if catch_row.avg_length else 0,
            "max_length": float(catch_row.max_length) if catch_row.max_length else 0,
            "total_points": int(catch_row.total_points or 0),
            "unique_participants": unique_catcher_count,
        },
        "species": species_data,
        "fish_scoring": fish_scoring,
        "bonus_points": bonus_points,
    }


@router.get("/{event_id}/export/participants")
async def export_participants(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> StreamingResponse:
    """
    Export participants list as CSV.
    Includes enrollment status, draw number, and contact info.
    """
    # Verify event exists and user has access
    event_query = select(Event).where(Event.id == event_id)
    result = await db.execute(event_query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.created_by_id != current_user.id and not current_user.profile.has_role("administrator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to export this event's data",
        )

    # Get enrollments with user data
    enrollments_query = (
        select(EventEnrollment)
        .options(
            selectinload(EventEnrollment.user).selectinload(UserAccount.profile)
        )
        .where(EventEnrollment.event_id == event_id)
        .order_by(EventEnrollment.enrollment_number)
    )
    enrollments_result = await db.execute(enrollments_query)
    enrollments = enrollments_result.scalars().all()

    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Enrollment #",
        "Draw #",
        "Status",
        "First Name",
        "Last Name",
        "Email",
        "Phone",
        "Enrolled At",
        "Approved At",
    ])

    # Data rows
    for enrollment in enrollments:
        profile = enrollment.user.profile if enrollment.user else None
        writer.writerow([
            enrollment.enrollment_number or "",
            enrollment.draw_number or "",
            enrollment.status,
            profile.first_name if profile else "",
            profile.last_name if profile else "",
            enrollment.user.email if enrollment.user else "",
            profile.phone if profile else "",
            enrollment.enrolled_at.strftime("%Y-%m-%d %H:%M") if enrollment.enrolled_at else "",
            enrollment.approved_at.strftime("%Y-%m-%d %H:%M") if enrollment.approved_at else "",
        ])

    # Return as streaming response
    output.seek(0)
    event_name_safe = sanitize_filename(event.name)
    start_date_str = event.start_date.strftime("%d%m%Y") if event.start_date else "nodate"
    filename = f"{event_name_safe}_{start_date_str}_participants.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{event_id}/export/catches")
async def export_catches(
    event_id: int,
    status_filter: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> StreamingResponse:
    """
    Export all catches as CSV.
    Includes fish details, measurements, points, and validation status.
    """
    # Verify event exists and user has access
    event_query = select(Event).where(Event.id == event_id)
    result = await db.execute(event_query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.created_by_id != current_user.id and not current_user.profile.has_any_role("administrator", "validator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to export this event's data",
        )

    # Get catches with related data
    catches_query = (
        select(Catch)
        .options(
            selectinload(Catch.user).selectinload(UserAccount.profile),
            selectinload(Catch.fish),
        )
        .where(Catch.event_id == event_id)
    )

    if status_filter:
        catches_query = catches_query.where(Catch.status == status_filter)

    catches_query = catches_query.order_by(Catch.submitted_at.desc())
    catches_result = await db.execute(catches_query)
    catches = catches_result.scalars().all()

    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Catch ID",
        "Participant",
        "Email",
        "Fish Species",
        "Length (cm)",
        "Weight (g)",
        "Points",
        "Status",
        "Submitted At",
        "Validated At",
        "Validated By",
        "Rejection Reason",
        "Latitude",
        "Longitude",
    ])

    # Data rows
    for catch in catches:
        profile = catch.user.profile if catch.user else None
        participant_name = f"{profile.first_name} {profile.last_name}" if profile else ""

        writer.writerow([
            catch.id,
            participant_name,
            catch.user.email if catch.user else "",
            catch.fish.name if catch.fish else "",
            catch.length or "",
            catch.weight or "",
            catch.points or 0,
            catch.status,
            catch.submitted_at.strftime("%Y-%m-%d %H:%M:%S") if catch.submitted_at else "",
            catch.validated_at.strftime("%Y-%m-%d %H:%M:%S") if catch.validated_at else "",
            catch.validated_by_id or "",
            catch.rejection_reason or "",
            catch.location_lat or "",
            catch.location_lng or "",
        ])

    # Return as streaming response
    output.seek(0)
    event_name_safe = sanitize_filename(event.name)
    start_date_str = event.start_date.strftime("%d%m%Y") if event.start_date else "nodate"
    status_suffix = f"_{status_filter}" if status_filter else ""
    filename = f"{event_name_safe}_{start_date_str}_catches{status_suffix}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{event_id}/export/leaderboard")
async def export_leaderboard(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> StreamingResponse:
    """
    Export final leaderboard/scoreboard as CSV.
    Includes rankings, scores, catch counts, and tiebreaker info.
    """
    # Verify event exists and user has access
    event_query = select(Event).where(Event.id == event_id)
    result = await db.execute(event_query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.created_by_id != current_user.id and not current_user.profile.has_any_role("administrator", "validator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to export this event's data",
        )

    # Get scoreboard entries
    scoreboard_query = (
        select(EventScoreboard)
        .options(
            selectinload(EventScoreboard.user).selectinload(UserAccount.profile)
        )
        .where(EventScoreboard.event_id == event_id)
        .order_by(EventScoreboard.rank)
    )
    scoreboard_result = await db.execute(scoreboard_query)
    scoreboard = scoreboard_result.scalars().all()

    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Rank",
        "Participant",
        "Email",
        "Total Points",
        "Bonus Points",
        "Penalty Points",
        "Total Catches",
        "Species Count",
        "Best Catch (cm)",
        "Average Length (cm)",
        "First Catch Time",
    ])

    # Data rows
    for entry in scoreboard:
        profile = entry.user.profile if entry.user else None
        participant_name = f"{profile.first_name} {profile.last_name}" if profile else ""

        writer.writerow([
            entry.rank,
            participant_name,
            entry.user.email if entry.user else "",
            entry.total_points or 0,
            entry.bonus_points or 0,
            entry.penalty_points or 0,
            entry.total_catches or 0,
            entry.species_count or 0,
            entry.best_catch_length or "",
            round(entry.average_length, 2) if entry.average_length else "",
            entry.first_catch_time.strftime("%Y-%m-%d %H:%M:%S") if entry.first_catch_time else "",
        ])

    # Return as streaming response
    output.seek(0)
    event_name_safe = sanitize_filename(event.name)
    start_date_str = event.start_date.strftime("%d%m%Y") if event.start_date else "nodate"
    filename = f"{event_name_safe}_{start_date_str}_leaderboard.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{event_id}/export/summary")
async def export_summary(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> StreamingResponse:
    """
    Export event summary report as CSV.
    Includes event info, participant counts, catch summary by species.
    """
    # Get stats first
    stats = await get_event_stats(event_id, db, current_user)

    # Verify event exists
    event_query = select(Event).where(Event.id == event_id)
    result = await db.execute(event_query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    # Create CSV with multiple sections
    output = io.StringIO()
    writer = csv.writer(output)

    # Event Info Section
    writer.writerow(["=== EVENT INFORMATION ==="])
    writer.writerow(["Name", stats["event"]["name"]])
    writer.writerow(["Status", stats["event"]["status"]])
    writer.writerow(["Event Type", stats["event"]["event_type"]])
    writer.writerow(["Scoring Method", stats["event"]["scoring_method"]])
    writer.writerow(["Start Date", stats["event"]["start_date"]])
    writer.writerow(["End Date", stats["event"]["end_date"]])
    writer.writerow([])

    # Enrollment Section
    writer.writerow(["=== ENROLLMENT SUMMARY ==="])
    writer.writerow(["Total Enrolled", stats["enrollments"]["total"]])
    writer.writerow(["Approved", stats["enrollments"]["approved"]])
    writer.writerow(["Pending", stats["enrollments"]["pending"]])
    writer.writerow([])

    # Catch Section
    writer.writerow(["=== CATCH SUMMARY ==="])
    writer.writerow(["Total Catches", stats["catches"]["total"]])
    writer.writerow(["Approved", stats["catches"]["approved"]])
    writer.writerow(["Pending", stats["catches"]["pending"]])
    writer.writerow(["Rejected", stats["catches"]["rejected"]])
    writer.writerow(["Unique Participants", stats["catches"]["unique_participants"]])
    writer.writerow(["Average Length (cm)", stats["catches"]["avg_length"]])
    writer.writerow(["Max Length (cm)", stats["catches"]["max_length"]])
    writer.writerow(["Total Points", stats["catches"]["total_points"]])
    writer.writerow([])

    # Species Breakdown
    writer.writerow(["=== CATCHES BY SPECIES ==="])
    writer.writerow(["Species", "Count", "Avg Length", "Max Length"])
    for species in stats["species"]:
        writer.writerow([
            species["name"],
            species["count"],
            species["avg_length"],
            species["max_length"],
        ])
    writer.writerow([])

    # Fish Scoring Config
    if stats["fish_scoring"]:
        writer.writerow(["=== FISH SCORING CONFIGURATION ==="])
        writer.writerow(["Species", "Catch Slots", "Min Length", "Under Min Points"])
        for fs in stats["fish_scoring"]:
            writer.writerow([
                fs["fish_name"],
                fs["catch_slots"],
                fs["min_length"],
                fs["under_min_points"],
            ])
        writer.writerow([])

    # Bonus Points Config
    if stats["bonus_points"]:
        writer.writerow(["=== SPECIES BONUS POINTS ==="])
        writer.writerow(["Species Count", "Bonus Points"])
        for bp in stats["bonus_points"]:
            writer.writerow([bp["species_count"], bp["bonus_points"]])

    # Return as streaming response
    output.seek(0)
    event_name_safe = sanitize_filename(event.name)
    start_date_str = event.start_date.strftime("%d%m%Y") if event.start_date else "nodate"
    filename = f"{event_name_safe}_{start_date_str}_summary.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
