"""Trout Shore Fishing (TSF) competition management endpoints.

This module provides endpoints for managing Trout Shore Fishing competitions,
which use multi-day positional scoring with sectors and legs.

Key features:
- Event settings configuration
- Multi-day competition structure
- Leg-based positional scoring
- Sector rotation
- Daily and final standings calculation
- Ranking movements tracking
"""

from datetime import datetime, timezone, date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.core.permissions import OrganizerOrAdmin, EventOwnerOrAdmin
from app.core.i18n import get_error_message

from app.models.user import UserAccount, UserProfile
from app.models.event import Event, EventStatus
from app.models.enrollment import EventEnrollment
from decimal import Decimal

from app.models.trout_shore import (
    TSFEventSettings,
    TSFEventPointConfig,
    TSFDay,
    TSFLeg,
    TSFLineup,
    TSFLegPosition,
    TSFDayStanding,
    TSFFinalStanding,
    TSFSectorValidator,
    TSFDayStatus,
    TSFLegStatus,
)

from app.schemas.trout_shore import (
    # Point Config
    TSFEventPointConfigResponse,
    TSFEventPointConfigUpdate,
    # Settings
    TSFEventSettingsCreate,
    TSFEventSettingsUpdate,
    TSFEventSettingsResponse,
    # Days
    TSFDayResponse,
    TSFDayListResponse,
    TSFDayUpdate,
    # Legs
    TSFLegResponse,
    TSFLegListResponse,
    TSFLegUpdate,
    # Lineup
    TSFLineupResponse,
    TSFLineupListResponse,
    # Positions
    TSFLegPositionResponse,
    TSFLegPositionListResponse,
    TSFLegPositionCreate,
    TSFLegPositionUpdate,
    TSFSubmitPositionsRequest,
    # Standings
    TSFDayStandingResponse,
    TSFDayStandingListResponse,
    TSFFinalStandingResponse,
    TSFFinalStandingListResponse,
    # Rankings
    TSFRankingMovementResponse,
    TSFRankingUpdateResponse,
    # Generate
    TSFGenerateDaysRequest,
    TSFGenerateDaysResponse,
    # Calculate
    TSFCalculateStandingsRequest,
    TSFCalculateStandingsResponse,
    # Sector Validators
    TSFSectorValidatorCreate,
    TSFSectorValidatorUpdate,
    TSFSectorValidatorResponse,
    TSFSectorValidatorListResponse,
    # Enums
    TSFDayStatusAPI,
    TSFLegStatusAPI,
)

from app.schemas.common import MessageResponse

router = APIRouter()


# =============================================================================
# Helper Functions
# =============================================================================

async def get_tsf_event(
    event_id: int,
    db: AsyncSession,
    request: Request,
    require_settings: bool = True,
) -> Event:
    """Get event and verify it's a TSF competition."""
    query = (
        select(Event)
        .options(selectinload(Event.event_type))
        .options(selectinload(Event.tsf_settings))
        .where(Event.id == event_id, Event.is_deleted == False)
    )
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("tsf_event_not_found", request),
        )

    # Verify event type is TSF
    if event.event_type and event.event_type.code not in ["trout_shore"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=get_error_message("tsf_invalid_event_type", request),
        )

    if require_settings and not event.tsf_settings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=get_error_message("tsf_settings_not_found", request),
        )

    return event


# =============================================================================
# Event Point Config Endpoints (Per-event customizable point values)
# =============================================================================

@router.get("/events/{event_id}/point-config", response_model=TSFEventPointConfigResponse)
async def get_tsf_point_config(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get point configuration for a TSF event.

    Returns the event's custom point values if configured,
    or the default values (V=3.0, T=1.5, T0=1.0, L=0.5, L0=0.0).
    """
    # Verify event exists and is TSF
    await get_tsf_event(event_id, db, request, require_settings=False)

    # Get custom config if exists
    query = select(TSFEventPointConfig).where(TSFEventPointConfig.event_id == event_id)
    result = await db.execute(query)
    config = result.scalar_one_or_none()

    if not config:
        # Return defaults
        return {
            "victory_points": Decimal("3.0"),
            "tie_points": Decimal("1.5"),
            "tie_zero_points": Decimal("1.0"),
            "loss_points": Decimal("0.5"),
            "loss_zero_points": Decimal("0.0"),
            "is_default": True,
        }

    return {
        "victory_points": config.victory_points,
        "tie_points": config.tie_points,
        "tie_zero_points": config.tie_zero_points,
        "loss_points": config.loss_points,
        "loss_zero_points": config.loss_zero_points,
        "is_default": False,
    }


@router.put("/events/{event_id}/point-config", response_model=TSFEventPointConfigResponse)
async def update_tsf_point_config(
    event_id: int,
    data: TSFEventPointConfigUpdate,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Update point configuration for a TSF event.

    Only the event organizer or administrator can modify point values.
    Creates a new config if one doesn't exist, or updates the existing one.

    Point values must follow logical ordering:
    victory >= tie >= tie_zero >= loss >= loss_zero
    """
    # Verify event exists and is TSF
    await get_tsf_event(event_id, db, request, require_settings=False)

    # Get or create config
    query = select(TSFEventPointConfig).where(TSFEventPointConfig.event_id == event_id)
    result = await db.execute(query)
    config = result.scalar_one_or_none()

    if config:
        # Update existing config
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if value is not None:
                setattr(config, key, value)
    else:
        # Create new config with provided values (or defaults)
        config = TSFEventPointConfig(
            event_id=event_id,
            victory_points=data.victory_points if data.victory_points is not None else Decimal("3.0"),
            tie_points=data.tie_points if data.tie_points is not None else Decimal("1.5"),
            tie_zero_points=data.tie_zero_points if data.tie_zero_points is not None else Decimal("1.0"),
            loss_points=data.loss_points if data.loss_points is not None else Decimal("0.5"),
            loss_zero_points=data.loss_zero_points if data.loss_zero_points is not None else Decimal("0.0"),
        )
        db.add(config)

    await db.commit()
    await db.refresh(config)

    return {
        "victory_points": config.victory_points,
        "tie_points": config.tie_points,
        "tie_zero_points": config.tie_zero_points,
        "loss_points": config.loss_points,
        "loss_zero_points": config.loss_zero_points,
        "is_default": False,
    }


@router.delete("/events/{event_id}/point-config", response_model=MessageResponse)
async def reset_tsf_point_config(
    event_id: int,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Reset point configuration to defaults by deleting custom config.

    After reset, the event will use the global default point values.
    """
    # Verify event exists and is TSF
    await get_tsf_event(event_id, db, request, require_settings=False)

    # Delete custom config if exists
    query = select(TSFEventPointConfig).where(TSFEventPointConfig.event_id == event_id)
    result = await db.execute(query)
    config = result.scalar_one_or_none()

    if config:
        await db.delete(config)
        await db.commit()
        return {"message": "Point configuration reset to defaults"}

    return {"message": "Event was already using default point values"}


# =============================================================================
# Event Settings Endpoints
# =============================================================================

@router.post(
    "/events/{event_id}/settings",
    response_model=TSFEventSettingsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_event_settings(
    event_id: int,
    data: TSFEventSettingsCreate,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> TSFEventSettings:
    """
    Create TSF settings for an event.

    Settings include:
    - Number of competition days
    - Number of sectors
    - Legs per day
    - Scoring direction (lower = better)
    - Tiebreaker rules
    - Sector rotation patterns
    """
    event = await get_tsf_event(event_id, db, request, require_settings=False)

    if event.tsf_settings:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="TSF settings already exist for this event. Use PATCH to update.",
        )

    settings = TSFEventSettings(
        event_id=event_id,
        number_of_days=data.number_of_days,
        number_of_sectors=data.number_of_sectors,
        participants_per_sector=data.participants_per_sector,
        legs_per_day=data.legs_per_day,
        scoring_direction=data.scoring_direction,
        ghost_position_penalty=data.ghost_position_penalty,
        rotate_sectors_daily=data.rotate_sectors_daily,
        seat_rotation_pattern=data.seat_rotation_pattern,
        tiebreaker_rules=data.tiebreaker_rules,
        additional_rules=data.additional_rules,
    )

    db.add(settings)
    await db.commit()
    await db.refresh(settings)

    return settings


@router.get("/events/{event_id}/settings", response_model=TSFEventSettingsResponse)
async def get_event_settings(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TSFEventSettings:
    """Get TSF settings for an event."""
    event = await get_tsf_event(event_id, db, request)
    return event.tsf_settings


@router.patch("/events/{event_id}/settings", response_model=TSFEventSettingsResponse)
async def update_event_settings(
    event_id: int,
    data: TSFEventSettingsUpdate,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> TSFEventSettings:
    """Update TSF settings for an event."""
    event = await get_tsf_event(event_id, db, request)
    settings = event.tsf_settings

    # Check if days already exist
    day_count_query = select(func.count()).select_from(TSFDay).where(
        TSFDay.event_id == event_id
    )
    day_result = await db.execute(day_count_query)
    has_days = day_result.scalar() > 0

    update_data = data.model_dump(exclude_unset=True)

    # Prevent changing critical settings after days are generated
    if has_days:
        restricted_fields = ["number_of_days", "number_of_sectors", "legs_per_day"]
        for field in restricted_fields:
            if field in update_data:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot change {field} after days are generated",
                )

    for field, value in update_data.items():
        if hasattr(settings, field):
            setattr(settings, field, value)

    settings.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(settings)

    return settings


# =============================================================================
# Day Endpoints
# =============================================================================

@router.get("/events/{event_id}/days", response_model=TSFDayListResponse)
async def list_days(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all competition days for a TSF event."""
    await get_tsf_event(event_id, db, request)

    query = (
        select(TSFDay)
        .where(TSFDay.event_id == event_id)
        .order_by(TSFDay.day_number)
    )
    result = await db.execute(query)
    days = result.scalars().all()

    # Get leg counts
    leg_counts_query = (
        select(
            TSFLeg.day_id,
            func.count().label('total'),
            func.count().filter(TSFLeg.status == TSFLegStatus.COMPLETED.value).label('completed'),
        )
        .where(TSFLeg.event_id == event_id)
        .group_by(TSFLeg.day_id)
    )
    leg_result = await db.execute(leg_counts_query)
    leg_counts = {row.day_id: (row.total, row.completed) for row in leg_result}

    current_day = None
    items = []

    for day in days:
        total_legs, completed_legs = leg_counts.get(day.id, (0, 0))

        if day.status == TSFDayStatus.IN_PROGRESS.value:
            current_day = day.day_number

        items.append(TSFDayResponse(
            id=day.id,
            event_id=day.event_id,
            day_number=day.day_number,
            scheduled_date=day.scheduled_date,
            start_time=day.start_time,
            end_time=day.end_time,
            status=TSFDayStatusAPI(day.status),
            weather_conditions=day.weather_conditions,
            notes=day.notes,
            created_at=day.created_at,
            updated_at=day.updated_at,
            legs_count=total_legs,
            completed_legs=completed_legs,
        ))

    return {
        "items": items,
        "total": len(items),
        "current_day": current_day,
    }


@router.post(
    "/events/{event_id}/days/generate",
    response_model=TSFGenerateDaysResponse,
)
async def generate_days(
    event_id: int,
    data: TSFGenerateDaysRequest,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Generate competition days and legs for a TSF event.

    Creates the full day/leg structure based on settings.
    """
    event = await get_tsf_event(event_id, db, request)
    settings = event.tsf_settings

    # Check if days already exist
    existing_query = select(func.count()).select_from(TSFDay).where(
        TSFDay.event_id == event_id
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar() > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Days have already been generated for this event",
        )

    created_days = []
    start_date = data.start_date or date.today()

    for day_num in range(1, settings.number_of_days + 1):
        day = TSFDay(
            event_id=event_id,
            day_number=day_num,
            scheduled_date=start_date + timedelta(days=day_num - 1),
            status=TSFDayStatus.SCHEDULED.value,
        )
        db.add(day)
        await db.flush()

        # Create legs for this day
        for leg_num in range(1, settings.legs_per_day + 1):
            leg = TSFLeg(
                event_id=event_id,
                day_id=day.id,
                day_number=day_num,
                leg_number=leg_num,
                status=TSFLegStatus.SCHEDULED.value,
            )
            db.add(leg)

        created_days.append(day)

    await db.commit()

    # Refresh days
    for day in created_days:
        await db.refresh(day)

    day_responses = [
        TSFDayResponse(
            id=day.id,
            event_id=day.event_id,
            day_number=day.day_number,
            scheduled_date=day.scheduled_date,
            start_time=day.start_time,
            end_time=day.end_time,
            status=TSFDayStatusAPI(day.status),
            weather_conditions=day.weather_conditions,
            notes=day.notes,
            created_at=day.created_at,
            updated_at=day.updated_at,
            legs_count=settings.legs_per_day,
            completed_legs=0,
        )
        for day in created_days
    ]

    return {
        "message": f"Created {len(created_days)} days with {settings.legs_per_day} legs each",
        "days_created": len(created_days),
        "legs_per_day": settings.legs_per_day,
        "total_legs": len(created_days) * settings.legs_per_day,
        "days": day_responses,
    }


@router.patch("/events/{event_id}/days/{day_number}", response_model=TSFDayResponse)
async def update_day(
    event_id: int,
    day_number: int,
    data: TSFDayUpdate,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> TSFDay:
    """Update a competition day."""
    event = await get_tsf_event(event_id, db, request)
    settings = event.tsf_settings

    if day_number < 1 or day_number > settings.number_of_days:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=get_error_message("tsf_invalid_day_number", request, max=settings.number_of_days),
        )

    query = select(TSFDay).where(
        TSFDay.event_id == event_id,
        TSFDay.day_number == day_number,
    )
    result = await db.execute(query)
    day = result.scalar_one_or_none()

    if not day:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("tsf_day_not_found", request),
        )

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "status" and value:
            setattr(day, field, value.value)
        elif hasattr(day, field):
            setattr(day, field, value)

    day.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(day)

    return day


@router.post("/events/{event_id}/days/{day_number}/start", response_model=TSFDayResponse)
async def start_day(
    event_id: int,
    day_number: int,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> TSFDay:
    """Start a competition day."""
    event = await get_tsf_event(event_id, db, request)

    query = select(TSFDay).where(
        TSFDay.event_id == event_id,
        TSFDay.day_number == day_number,
    )
    result = await db.execute(query)
    day = result.scalar_one_or_none()

    if not day:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("tsf_day_not_found", request),
        )

    if day.status != TSFDayStatus.SCHEDULED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Day is not scheduled (current status: {day.status})",
        )

    day.status = TSFDayStatus.IN_PROGRESS.value
    day.start_time = datetime.now(timezone.utc)
    day.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(day)

    return day


@router.post("/events/{event_id}/days/{day_number}/complete", response_model=TSFDayResponse)
async def complete_day(
    event_id: int,
    day_number: int,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> TSFDay:
    """Complete a competition day and calculate standings."""
    event = await get_tsf_event(event_id, db, request)

    query = select(TSFDay).where(
        TSFDay.event_id == event_id,
        TSFDay.day_number == day_number,
    )
    result = await db.execute(query)
    day = result.scalar_one_or_none()

    if not day:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("tsf_day_not_found", request),
        )

    if day.status != TSFDayStatus.IN_PROGRESS.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Day is not in progress (current status: {day.status})",
        )

    day.status = TSFDayStatus.COMPLETED.value
    day.end_time = datetime.now(timezone.utc)
    day.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(day)

    # TODO: Trigger standings calculation

    return day


# =============================================================================
# Leg Endpoints
# =============================================================================

@router.get("/events/{event_id}/days/{day_number}/legs", response_model=TSFLegListResponse)
async def list_legs(
    event_id: int,
    day_number: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all legs for a competition day."""
    await get_tsf_event(event_id, db, request)

    query = (
        select(TSFLeg)
        .where(TSFLeg.event_id == event_id, TSFLeg.day_number == day_number)
        .order_by(TSFLeg.leg_number)
    )
    result = await db.execute(query)
    legs = result.scalars().all()

    items = [
        TSFLegResponse(
            id=leg.id,
            event_id=leg.event_id,
            day_id=leg.day_id,
            day_number=leg.day_number,
            leg_number=leg.leg_number,
            scheduled_start=leg.scheduled_start,
            actual_start=leg.actual_start,
            actual_end=leg.actual_end,
            status=TSFLegStatusAPI(leg.status),
            created_at=leg.created_at,
            updated_at=leg.updated_at,
        )
        for leg in legs
    ]

    return {
        "items": items,
        "total": len(items),
        "day_number": day_number,
    }


@router.post(
    "/events/{event_id}/days/{day_number}/legs/{leg_number}/start",
    response_model=TSFLegResponse,
)
async def start_leg(
    event_id: int,
    day_number: int,
    leg_number: int,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> TSFLeg:
    """Start a competition leg."""
    await get_tsf_event(event_id, db, request)

    query = select(TSFLeg).where(
        TSFLeg.event_id == event_id,
        TSFLeg.day_number == day_number,
        TSFLeg.leg_number == leg_number,
    )
    result = await db.execute(query)
    leg = result.scalar_one_or_none()

    if not leg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("tsf_leg_not_found", request),
        )

    if leg.status != TSFLegStatus.SCHEDULED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Leg is not scheduled (current status: {leg.status})",
        )

    leg.status = TSFLegStatus.IN_PROGRESS.value
    leg.actual_start = datetime.now(timezone.utc)
    leg.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(leg)

    return leg


@router.post(
    "/events/{event_id}/days/{day_number}/legs/{leg_number}/complete",
    response_model=TSFLegResponse,
)
async def complete_leg(
    event_id: int,
    day_number: int,
    leg_number: int,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> TSFLeg:
    """Complete a competition leg."""
    await get_tsf_event(event_id, db, request)

    query = select(TSFLeg).where(
        TSFLeg.event_id == event_id,
        TSFLeg.day_number == day_number,
        TSFLeg.leg_number == leg_number,
    )
    result = await db.execute(query)
    leg = result.scalar_one_or_none()

    if not leg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("tsf_leg_not_found", request),
        )

    if leg.status != TSFLegStatus.IN_PROGRESS.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Leg is not in progress (current status: {leg.status})",
        )

    leg.status = TSFLegStatus.COMPLETED.value
    leg.actual_end = datetime.now(timezone.utc)
    leg.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(leg)

    return leg


# =============================================================================
# Lineup Endpoints
# =============================================================================

@router.get("/events/{event_id}/lineups", response_model=TSFLineupListResponse)
async def list_lineups(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get all lineups for a TSF event."""
    event = await get_tsf_event(event_id, db, request)
    settings = event.tsf_settings

    query = (
        select(TSFLineup)
        .options(selectinload(TSFLineup.user).selectinload(UserAccount.profile))
        .where(TSFLineup.event_id == event_id)
        .order_by(TSFLineup.group_number, TSFLineup.seat_index)
    )
    result = await db.execute(query)
    lineups = result.scalars().all()

    items = []
    has_ghost = False
    groups = set()

    for lineup in lineups:
        if lineup.is_ghost:
            has_ghost = True
        groups.add(lineup.group_number)

        item = TSFLineupResponse(
            id=lineup.id,
            event_id=lineup.event_id,
            user_id=lineup.user_id,
            enrollment_id=lineup.enrollment_id,
            draw_number=lineup.draw_number,
            group_number=lineup.group_number,
            seat_index=lineup.seat_index,
            is_ghost=lineup.is_ghost,
            created_at=lineup.created_at,
            user_name=lineup.user.profile.full_name if lineup.user and lineup.user.profile else None,
            user_avatar=lineup.user.avatar_url if lineup.user else None,
        )
        items.append(item)

    participants_per_group = len(items) // len(groups) if groups else 0

    return {
        "items": items,
        "total": len(items),
        "has_ghost": has_ghost,
        "groups": len(groups),
        "participants_per_group": participants_per_group,
    }


@router.post("/events/{event_id}/lineups/generate", response_model=TSFLineupListResponse)
async def generate_lineups(
    event_id: int,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Generate lineups for a TSF event.

    Distributes participants into sectors/groups based on settings.
    Adds ghost participants if needed for even distribution.
    """
    event = await get_tsf_event(event_id, db, request)
    settings = event.tsf_settings

    # Check if lineups already exist
    existing_query = select(func.count()).select_from(TSFLineup).where(
        TSFLineup.event_id == event_id
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar() > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Lineups have already been generated for this event",
        )

    # Get enrolled participants
    enrollments_query = (
        select(EventEnrollment)
        .options(selectinload(EventEnrollment.user).selectinload(UserAccount.profile))
        .where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.status == "enrolled",
        )
        .order_by(EventEnrollment.draw_number.nullslast(), EventEnrollment.id)
    )
    enrollments_result = await db.execute(enrollments_query)
    enrollments = list(enrollments_result.scalars().all())

    if len(enrollments) < settings.number_of_sectors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not enough participants (minimum: {settings.number_of_sectors})",
        )

    # Calculate how many participants per sector
    num_participants = len(enrollments)
    participants_per_sector = settings.participants_per_sector or (
        (num_participants + settings.number_of_sectors - 1) // settings.number_of_sectors
    )

    # Add ghost participants if needed for even distribution
    total_needed = participants_per_sector * settings.number_of_sectors
    num_ghosts = total_needed - num_participants if total_needed > num_participants else 0

    created_lineups = []
    draw_number = 1

    # Distribute participants across sectors
    for sector in range(1, settings.number_of_sectors + 1):
        seat_index = 1

        # Assign real participants to this sector
        start_idx = (sector - 1) * participants_per_sector
        end_idx = min(start_idx + participants_per_sector, num_participants)

        for i in range(start_idx, end_idx):
            if i < len(enrollments):
                enrollment = enrollments[i]
                lineup = TSFLineup(
                    event_id=event_id,
                    user_id=enrollment.user_id,
                    enrollment_id=enrollment.id,
                    draw_number=draw_number,
                    group_number=sector,
                    seat_index=seat_index,
                    is_ghost=False,
                )
                db.add(lineup)
                created_lineups.append(lineup)
                draw_number += 1
                seat_index += 1

        # Fill remaining seats with ghosts if needed
        while seat_index <= participants_per_sector:
            lineup = TSFLineup(
                event_id=event_id,
                user_id=None,
                enrollment_id=None,
                draw_number=draw_number,
                group_number=sector,
                seat_index=seat_index,
                is_ghost=True,
            )
            db.add(lineup)
            created_lineups.append(lineup)
            draw_number += 1
            seat_index += 1

    await db.commit()

    # Refresh lineups
    for lineup in created_lineups:
        await db.refresh(lineup)

    items = []
    has_ghost = num_ghosts > 0

    for lineup in created_lineups:
        item = TSFLineupResponse(
            id=lineup.id,
            event_id=lineup.event_id,
            user_id=lineup.user_id,
            enrollment_id=lineup.enrollment_id,
            draw_number=lineup.draw_number,
            group_number=lineup.group_number,
            seat_index=lineup.seat_index,
            is_ghost=lineup.is_ghost,
            created_at=lineup.created_at,
        )
        items.append(item)

    return {
        "items": items,
        "total": len(items),
        "has_ghost": has_ghost,
        "groups": settings.number_of_sectors,
        "participants_per_group": participants_per_sector,
    }


# =============================================================================
# Position Endpoints
# =============================================================================

@router.get(
    "/events/{event_id}/days/{day_number}/legs/{leg_number}/positions",
    response_model=TSFLegPositionListResponse,
)
async def list_positions(
    event_id: int,
    day_number: int,
    leg_number: int,
    request: Request,
    group_number: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all positions for a leg, optionally filtered by group."""
    await get_tsf_event(event_id, db, request)

    # Get leg
    leg_query = select(TSFLeg).where(
        TSFLeg.event_id == event_id,
        TSFLeg.day_number == day_number,
        TSFLeg.leg_number == leg_number,
    )
    leg_result = await db.execute(leg_query)
    leg = leg_result.scalar_one_or_none()

    if not leg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("tsf_leg_not_found", request),
        )

    query = (
        select(TSFLegPosition)
        .options(selectinload(TSFLegPosition.user).selectinload(UserAccount.profile))
        .where(TSFLegPosition.leg_id == leg.id)
    )

    if group_number:
        query = query.where(TSFLegPosition.group_number == group_number)

    query = query.order_by(TSFLegPosition.group_number, TSFLegPosition.position_value)

    result = await db.execute(query)
    positions = result.scalars().all()

    items = [
        TSFLegPositionResponse(
            id=pos.id,
            event_id=pos.event_id,
            leg_id=pos.leg_id,
            user_id=pos.user_id,
            group_number=pos.group_number,
            day_number=pos.day_number,
            leg_number=pos.leg_number,
            seat_index=pos.seat_index,
            position_value=pos.position_value,
            fish_count=pos.fish_count,
            total_length=pos.total_length,
            best_checksum=pos.best_checksum,
            worst_checksum=pos.worst_checksum,
            running_total=pos.running_total,
            is_ghost=pos.is_ghost,
            is_dnf=pos.is_dnf,
            created_at=pos.created_at,
            updated_at=pos.updated_at,
            user_name=pos.user.profile.full_name if pos.user and pos.user.profile else None,
        )
        for pos in positions
    ]

    return {
        "items": items,
        "total": len(items),
        "leg_id": leg.id,
        "day_number": day_number,
        "leg_number": leg_number,
    }


@router.post(
    "/events/{event_id}/days/{day_number}/legs/{leg_number}/positions",
    response_model=MessageResponse,
)
async def submit_positions(
    event_id: int,
    day_number: int,
    leg_number: int,
    data: TSFSubmitPositionsRequest,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Submit positions for a leg.

    This is typically done by the organizer/validator after the leg completes.
    Each participant's position (1st, 2nd, 3rd, etc.) within their group is recorded.
    """
    await get_tsf_event(event_id, db, request)

    # Get leg
    leg_query = select(TSFLeg).where(
        TSFLeg.event_id == event_id,
        TSFLeg.day_number == day_number,
        TSFLeg.leg_number == leg_number,
    )
    leg_result = await db.execute(leg_query)
    leg = leg_result.scalar_one_or_none()

    if not leg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("tsf_leg_not_found", request),
        )

    # Check leg is in progress or completed (positions can be updated during)
    if leg.status == TSFLegStatus.SCHEDULED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=get_error_message("tsf_leg_not_active", request),
        )

    # Create position records
    for pos_data in data.positions:
        position = TSFLegPosition(
            event_id=event_id,
            leg_id=leg.id,
            user_id=pos_data.user_id,
            group_number=pos_data.group_number,
            day_number=day_number,
            leg_number=leg_number,
            seat_index=pos_data.seat_index,
            position_value=pos_data.position_value,
            fish_count=pos_data.fish_count,
            total_length=pos_data.total_length,
            is_ghost=pos_data.is_ghost,
            is_dnf=pos_data.is_dnf,
        )
        db.add(position)

    await db.commit()

    return {
        "message": f"Submitted {len(data.positions)} positions for leg {leg_number}",
    }


@router.patch(
    "/events/{event_id}/days/{day_number}/legs/{leg_number}/positions/{position_id}",
    response_model=TSFLegPositionResponse,
)
async def edit_position(
    event_id: int,
    day_number: int,
    leg_number: int,
    position_id: int,
    data: TSFLegPositionUpdate,
    request: Request,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TSFLegPosition:
    """
    Edit a TSF position entry.

    Validators can edit their own entries during an active leg.
    Organizers can edit any entry at any time.

    Edit history is tracked with edited_by_id, edited_at, and previous values.
    """
    event = await get_tsf_event(event_id, db, request, require_settings=False)

    # Get the position
    position_query = (
        select(TSFLegPosition)
        .options(selectinload(TSFLegPosition.leg))
        .options(selectinload(TSFLegPosition.user).selectinload(UserAccount.profile))
        .where(
            TSFLegPosition.id == position_id,
            TSFLegPosition.event_id == event_id,
        )
    )
    position_result = await db.execute(position_query)
    position = position_result.scalar_one_or_none()

    if not position:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("tsf_position_not_found", request),
        )

    # Verify day and leg match
    if position.day_number != day_number or position.leg_number != leg_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Position does not belong to specified day/leg",
        )

    # Check permissions
    is_organizer = event.created_by_id == current_user.id
    is_admin_user = current_user.is_superuser or current_user.is_staff
    is_own_entry = position.validated_by_id == current_user.id
    leg_active = position.leg.status != TSFLegStatus.COMPLETED.value

    if not is_organizer and not is_admin_user:
        if not is_own_entry:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=get_error_message("cannot_edit_others_entry", request),
            )
        if not leg_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=get_error_message("leg_completed_cannot_edit", request),
            )

    # Store previous values for audit
    update_data = data.model_dump(exclude_unset=True)
    if 'fish_count' in update_data:
        position.previous_fish_count = position.fish_count
    if 'position_value' in update_data:
        position.previous_position_value = position.position_value

    # Track who edited and when
    position.edited_by_id = current_user.id
    position.edited_at = datetime.now(timezone.utc)

    # Apply updates
    for key, value in update_data.items():
        if hasattr(position, key):
            setattr(position, key, value)

    await db.commit()
    await db.refresh(position)

    return position


# =============================================================================
# Standings Endpoints
# =============================================================================

@router.get(
    "/events/{event_id}/days/{day_number}/standings",
    response_model=TSFDayStandingListResponse,
)
async def get_day_standings(
    event_id: int,
    day_number: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get standings for a specific competition day."""
    event = await get_tsf_event(event_id, db, request)

    # Get day
    day_query = select(TSFDay).where(
        TSFDay.event_id == event_id,
        TSFDay.day_number == day_number,
    )
    day_result = await db.execute(day_query)
    day = day_result.scalar_one_or_none()

    if not day:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("tsf_day_not_found", request),
        )

    query = (
        select(TSFDayStanding)
        .options(selectinload(TSFDayStanding.user).selectinload(UserAccount.profile))
        .where(TSFDayStanding.day_id == day.id)
        .order_by(TSFDayStanding.overall_rank.nullslast())
    )
    result = await db.execute(query)
    standings = result.scalars().all()

    items = []
    groups: dict[int, list] = {}

    for standing in standings:
        item = TSFDayStandingResponse(
            id=standing.id,
            event_id=standing.event_id,
            day_id=standing.day_id,
            day_number=standing.day_number,
            user_id=standing.user_id,
            group_number=standing.group_number,
            total_position_points=standing.total_position_points,
            legs_completed=standing.legs_completed,
            first_places=standing.first_places,
            second_places=standing.second_places,
            third_places=standing.third_places,
            best_single_leg=standing.best_single_leg,
            worst_single_leg=standing.worst_single_leg,
            total_fish_count=standing.total_fish_count,
            total_length=standing.total_length,
            sector_rank=standing.sector_rank,
            overall_rank=standing.overall_rank,
            leg_positions=standing.leg_positions or {},
            updated_at=standing.updated_at,
            user_name=standing.user.profile.full_name if standing.user and standing.user.profile else None,
            user_avatar=standing.user.avatar_url if standing.user else None,
        )
        items.append(item)

        if standing.group_number not in groups:
            groups[standing.group_number] = []
        groups[standing.group_number].append(item)

    return {
        "items": items,
        "total": len(items),
        "day_number": day_number,
        "groups": groups,
    }


@router.get("/events/{event_id}/standings/final", response_model=TSFFinalStandingListResponse)
async def get_final_standings(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get final standings for a TSF event."""
    event = await get_tsf_event(event_id, db, request)
    settings = event.tsf_settings

    # Count completed days
    completed_days_query = select(func.count()).select_from(TSFDay).where(
        TSFDay.event_id == event_id,
        TSFDay.status == TSFDayStatus.COMPLETED.value,
    )
    completed_result = await db.execute(completed_days_query)
    completed_days = completed_result.scalar()

    query = (
        select(TSFFinalStanding)
        .options(selectinload(TSFFinalStanding.user).selectinload(UserAccount.profile))
        .where(TSFFinalStanding.event_id == event_id)
        .order_by(TSFFinalStanding.final_rank.nullslast())
    )
    result = await db.execute(query)
    standings = result.scalars().all()

    items = [
        TSFFinalStandingResponse(
            id=standing.id,
            event_id=standing.event_id,
            user_id=standing.user_id,
            enrollment_id=standing.enrollment_id,
            group_number=standing.group_number,
            total_position_points=standing.total_position_points,
            days_completed=standing.days_completed,
            legs_completed=standing.legs_completed,
            total_first_places=standing.total_first_places,
            total_second_places=standing.total_second_places,
            total_third_places=standing.total_third_places,
            best_single_leg=standing.best_single_leg,
            worst_single_leg=standing.worst_single_leg,
            best_day_total=standing.best_day_total,
            worst_day_total=standing.worst_day_total,
            total_fish_count=standing.total_fish_count,
            total_length=standing.total_length,
            final_rank=standing.final_rank,
            day_totals=standing.day_totals or {},
            updated_at=standing.updated_at,
            user_name=standing.user.profile.full_name if standing.user and standing.user.profile else None,
            user_avatar=standing.user.avatar_url if standing.user else None,
        )
        for standing in standings
    ]

    return {
        "items": items,
        "total": len(items),
        "completed_days": completed_days,
        "total_days": settings.number_of_days,
    }


@router.post(
    "/events/{event_id}/standings/calculate",
    response_model=TSFCalculateStandingsResponse,
)
async def calculate_standings(
    event_id: int,
    data: TSFCalculateStandingsRequest,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Calculate standings for a day or final standings.

    This aggregates leg positions into standings.
    If day_number is provided, calculates day standings.
    If day_number is None, calculates final standings across all days.
    """
    await get_tsf_event(event_id, db, request)

    # TODO: Implement actual standings calculation logic
    # This would:
    # 1. For day standings: Sum position_values for each user in that day
    # 2. Apply tiebreaker rules (first_places, total_fish_count, etc.)
    # 3. Calculate sector_rank and overall_rank
    # 4. For final standings: Sum across all days

    standings_type = "day" if data.day_number else "final"

    return {
        "message": get_error_message("ranking_updated", request),
        "standings_type": standings_type,
        "day_number": data.day_number,
        "participants_ranked": 0,  # Would be actual count
    }


# =============================================================================
# Sector Validator Endpoints
# =============================================================================
# TSF competitions use sector validators/arbiters who enter results for ALL
# participants in their assigned sector. This is NOT self-validation - the
# fast-paced nature of TSF competitions requires dedicated validators.
# =============================================================================


@router.get(
    "/events/{event_id}/sector-validators",
    response_model=TSFSectorValidatorListResponse,
)
async def list_sector_validators(
    event_id: int,
    request: Request,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    List all sector validators for a TSF event.

    Returns validators assigned to each sector along with summary stats.
    """
    event = await get_tsf_event(event_id, db, request)

    # Get settings to know total sectors
    settings_query = select(TSFEventSettings).where(
        TSFEventSettings.event_id == event_id
    )
    settings_result = await db.execute(settings_query)
    settings = settings_result.scalar_one_or_none()

    total_sectors = settings.number_of_sectors if settings else 0

    # Get validators with user info
    query = (
        select(TSFSectorValidator)
        .options(
            selectinload(TSFSectorValidator.validator).selectinload(UserAccount.profile),
            selectinload(TSFSectorValidator.backup_validator).selectinload(UserAccount.profile),
        )
        .where(TSFSectorValidator.event_id == event_id)
        .order_by(TSFSectorValidator.sector_number)
    )
    result = await db.execute(query)
    validators = result.scalars().all()

    items = [
        TSFSectorValidatorResponse(
            id=v.id,
            event_id=v.event_id,
            validator_id=v.validator_id,
            sector_number=v.sector_number,
            backup_validator_id=v.backup_validator_id,
            is_active=v.is_active,
            created_at=v.created_at,
            validator_name=v.validator.profile.full_name if v.validator and v.validator.profile else None,
            validator_email=v.validator.email if v.validator else None,
            validator_avatar=v.validator.avatar_url if v.validator else None,
            backup_validator_name=v.backup_validator.profile.full_name if v.backup_validator and v.backup_validator.profile else None,
        )
        for v in validators
    ]

    return {
        "items": items,
        "total": len(items),
        "total_sectors": total_sectors,
        "assigned_sectors": len([v for v in validators if v.is_active]),
    }


@router.post(
    "/events/{event_id}/sector-validators",
    response_model=TSFSectorValidatorResponse,
    status_code=status.HTTP_201_CREATED,
)
async def assign_sector_validator(
    event_id: int,
    data: TSFSectorValidatorCreate,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> TSFSectorValidatorResponse:
    """
    Assign a validator to a sector.

    Only event organizers or admins can assign sector validators.
    Each sector can only have one active validator at a time.
    """
    event = await get_tsf_event(event_id, db, request)

    # Verify validator user exists
    validator_query = select(UserAccount).where(UserAccount.id == data.validator_id)
    validator_result = await db.execute(validator_query)
    validator_user = validator_result.scalar_one_or_none()

    if not validator_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("user_not_found", request),
        )

    # Check if sector already has a validator
    existing_query = select(TSFSectorValidator).where(
        and_(
            TSFSectorValidator.event_id == event_id,
            TSFSectorValidator.sector_number == data.sector_number,
        )
    )
    existing_result = await db.execute(existing_query)
    existing = existing_result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Sector {data.sector_number} already has a validator assigned",
        )

    # Check if user is already assigned to another sector in this event
    user_sector_query = select(TSFSectorValidator).where(
        and_(
            TSFSectorValidator.event_id == event_id,
            TSFSectorValidator.validator_id == data.validator_id,
        )
    )
    user_sector_result = await db.execute(user_sector_query)
    user_sector = user_sector_result.scalar_one_or_none()

    if user_sector:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User is already assigned as validator for sector {user_sector.sector_number}",
        )

    # Verify backup validator if provided
    backup_user = None
    if data.backup_validator_id:
        backup_query = select(UserAccount).options(
            selectinload(UserAccount.profile)
        ).where(UserAccount.id == data.backup_validator_id)
        backup_result = await db.execute(backup_query)
        backup_user = backup_result.scalar_one_or_none()

        if not backup_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Backup validator user not found",
            )

    # Load validator profile for response
    await db.refresh(validator_user, ["profile"])

    # Create sector validator
    sector_validator = TSFSectorValidator(
        event_id=event_id,
        validator_id=data.validator_id,
        sector_number=data.sector_number,
        backup_validator_id=data.backup_validator_id,
        is_active=data.is_active,
    )

    db.add(sector_validator)
    await db.commit()
    await db.refresh(sector_validator)

    return TSFSectorValidatorResponse(
        id=sector_validator.id,
        event_id=sector_validator.event_id,
        validator_id=sector_validator.validator_id,
        sector_number=sector_validator.sector_number,
        backup_validator_id=sector_validator.backup_validator_id,
        is_active=sector_validator.is_active,
        created_at=sector_validator.created_at,
        validator_name=validator_user.profile.full_name if validator_user.profile else None,
        validator_email=validator_user.email,
        validator_avatar=validator_user.avatar_url,
        backup_validator_name=backup_user.profile.full_name if backup_user and backup_user.profile else None,
    )


@router.patch(
    "/events/{event_id}/sector-validators/{sector_number}",
    response_model=TSFSectorValidatorResponse,
)
async def update_sector_validator(
    event_id: int,
    sector_number: int,
    data: TSFSectorValidatorUpdate,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> TSFSectorValidatorResponse:
    """
    Update a sector validator assignment.

    Can change the validator, backup validator, or active status.
    """
    await get_tsf_event(event_id, db, request)

    # Get existing validator assignment
    query = (
        select(TSFSectorValidator)
        .options(
            selectinload(TSFSectorValidator.validator).selectinload(UserAccount.profile),
            selectinload(TSFSectorValidator.backup_validator).selectinload(UserAccount.profile),
        )
        .where(
            and_(
                TSFSectorValidator.event_id == event_id,
                TSFSectorValidator.sector_number == sector_number,
            )
        )
    )
    result = await db.execute(query)
    sector_validator = result.scalar_one_or_none()

    if not sector_validator:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No validator assigned to sector {sector_number}",
        )

    # Update fields
    update_data = data.model_dump(exclude_unset=True)

    # If changing validator, verify new user exists and isn't already assigned
    if "validator_id" in update_data and update_data["validator_id"] != sector_validator.validator_id:
        new_validator_id = update_data["validator_id"]

        # Verify user exists
        validator_query = select(UserAccount).options(
            selectinload(UserAccount.profile)
        ).where(UserAccount.id == new_validator_id)
        validator_result = await db.execute(validator_query)
        validator_user = validator_result.scalar_one_or_none()

        if not validator_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Validator user not found",
            )

        # Check if user is already assigned to another sector
        user_sector_query = select(TSFSectorValidator).where(
            and_(
                TSFSectorValidator.event_id == event_id,
                TSFSectorValidator.validator_id == new_validator_id,
                TSFSectorValidator.sector_number != sector_number,
            )
        )
        user_sector_result = await db.execute(user_sector_query)
        user_sector = user_sector_result.scalar_one_or_none()

        if user_sector:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"User is already assigned as validator for sector {user_sector.sector_number}",
            )

    # If changing backup validator, verify user exists
    if "backup_validator_id" in update_data and update_data["backup_validator_id"]:
        backup_query = select(UserAccount).where(
            UserAccount.id == update_data["backup_validator_id"]
        )
        backup_result = await db.execute(backup_query)
        backup_user = backup_result.scalar_one_or_none()

        if not backup_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Backup validator user not found",
            )

    # Apply updates
    for key, value in update_data.items():
        setattr(sector_validator, key, value)

    await db.commit()

    # Reload with relationships
    await db.refresh(sector_validator, ["validator", "backup_validator"])
    if sector_validator.validator:
        await db.refresh(sector_validator.validator, ["profile"])
    if sector_validator.backup_validator:
        await db.refresh(sector_validator.backup_validator, ["profile"])

    return TSFSectorValidatorResponse(
        id=sector_validator.id,
        event_id=sector_validator.event_id,
        validator_id=sector_validator.validator_id,
        sector_number=sector_validator.sector_number,
        backup_validator_id=sector_validator.backup_validator_id,
        is_active=sector_validator.is_active,
        created_at=sector_validator.created_at,
        validator_name=sector_validator.validator.profile.full_name if sector_validator.validator and sector_validator.validator.profile else None,
        validator_email=sector_validator.validator.email if sector_validator.validator else None,
        validator_avatar=sector_validator.validator.avatar_url if sector_validator.validator else None,
        backup_validator_name=sector_validator.backup_validator.profile.full_name if sector_validator.backup_validator and sector_validator.backup_validator.profile else None,
    )


@router.delete(
    "/events/{event_id}/sector-validators/{sector_number}",
    response_model=MessageResponse,
)
async def remove_sector_validator(
    event_id: int,
    sector_number: int,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Remove a validator from a sector.

    This completely removes the assignment. To temporarily disable,
    use PATCH to set is_active=false instead.
    """
    await get_tsf_event(event_id, db, request)

    # Get existing validator assignment
    query = select(TSFSectorValidator).where(
        and_(
            TSFSectorValidator.event_id == event_id,
            TSFSectorValidator.sector_number == sector_number,
        )
    )
    result = await db.execute(query)
    sector_validator = result.scalar_one_or_none()

    if not sector_validator:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No validator assigned to sector {sector_number}",
        )

    await db.delete(sector_validator)
    await db.commit()

    return {"message": f"Validator removed from sector {sector_number}"}


@router.get(
    "/events/{event_id}/my-sector",
    response_model=TSFSectorValidatorResponse,
)
async def get_my_sector(
    event_id: int,
    request: Request,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TSFSectorValidatorResponse:
    """
    Get the sector assigned to the current user as validator.

    Returns 404 if the user is not assigned as a validator for this event.
    This endpoint is useful for validators to quickly find their sector.
    """
    await get_tsf_event(event_id, db, request)

    # Find sector where user is validator or backup
    query = (
        select(TSFSectorValidator)
        .options(
            selectinload(TSFSectorValidator.validator).selectinload(UserAccount.profile),
            selectinload(TSFSectorValidator.backup_validator).selectinload(UserAccount.profile),
        )
        .where(
            and_(
                TSFSectorValidator.event_id == event_id,
                (
                    (TSFSectorValidator.validator_id == current_user.id) |
                    (TSFSectorValidator.backup_validator_id == current_user.id)
                ),
            )
        )
    )
    result = await db.execute(query)
    sector_validator = result.scalar_one_or_none()

    if not sector_validator:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not assigned as a validator for any sector in this event",
        )

    return TSFSectorValidatorResponse(
        id=sector_validator.id,
        event_id=sector_validator.event_id,
        validator_id=sector_validator.validator_id,
        sector_number=sector_validator.sector_number,
        backup_validator_id=sector_validator.backup_validator_id,
        is_active=sector_validator.is_active,
        created_at=sector_validator.created_at,
        validator_name=sector_validator.validator.profile.full_name if sector_validator.validator and sector_validator.validator.profile else None,
        validator_email=sector_validator.validator.email if sector_validator.validator else None,
        validator_avatar=sector_validator.validator.avatar_url if sector_validator.validator else None,
        backup_validator_name=sector_validator.backup_validator.profile.full_name if sector_validator.backup_validator and sector_validator.backup_validator.profile else None,
    )


# =============================================================================
# Detailed Rankings Endpoints (Group Rankings with Leg-by-Leg and RANK.AVG)
# =============================================================================

@router.get("/events/{event_id}/rankings/groups")
async def get_group_rankings(
    event_id: int,
    request: Request,
    day_number: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get group rankings with leg-by-leg breakdown.

    Returns each group's participants ranked by total points (RANK.AVG style)
    with individual leg fish counts and points.
    """
    from app.services.ta_ranking import TSFRankingService

    event = await get_tsf_event(event_id, db, request)

    ranking_service = TSFRankingService(db)
    groups = await ranking_service.get_group_ranking_with_legs(event_id, day_number)

    return {
        "event_id": event_id,
        "day_number": day_number,
        "groups": groups,
        "total_groups": len(groups),
    }


@router.get("/events/{event_id}/rankings/final")
async def get_final_ranking(
    event_id: int,
    request: Request,
    exclude_ghosts: bool = True,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get final ranking across all groups.

    Returns overall event ranking sorted by total points (lower is better).
    """
    from app.services.ta_ranking import TSFRankingService

    event = await get_tsf_event(event_id, db, request)

    ranking_service = TSFRankingService(db)
    rankings = await ranking_service.get_final_ranking(event_id, exclude_ghosts)

    return {
        "event_id": event_id,
        "exclude_ghosts": exclude_ghosts,
        "rankings": rankings,
        "total_participants": len(rankings),
    }


@router.get("/events/{event_id}/statistics")
async def get_tsf_event_statistics(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get event-level statistics for TSF competition.

    Returns best groups (by points and fish), best participants,
    and total fish caught. Excludes ghost entries.
    """
    from app.services.ta_ranking import TSFRankingService

    event = await get_tsf_event(event_id, db, request)

    ranking_service = TSFRankingService(db)
    stats = await ranking_service.get_event_statistics(event_id)

    return stats


@router.post("/events/{event_id}/rankings/recalculate-leg")
async def recalculate_leg_positions(
    event_id: int,
    day_number: int,
    group_number: int,
    leg_number: int,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Recalculate RANK.AVG positions for a specific leg.

    This is called after fish counts are entered to compute position points.
    Higher fish count = better position (lower number).
    Ties get average rank (RANK.AVG).
    """
    from app.services.ta_ranking import TSFRankingService

    event = await get_tsf_event(event_id, db, request)

    ranking_service = TSFRankingService(db)
    await ranking_service.recalc_leg_positions(
        event_id, day_number, group_number, leg_number
    )

    await db.commit()

    return {
        "message": "Leg positions recalculated",
        "event_id": event_id,
        "day_number": day_number,
        "group_number": group_number,
        "leg_number": leg_number,
    }
