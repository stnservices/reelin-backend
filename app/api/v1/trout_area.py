"""Trout Area (TA) competition management endpoints.

This module provides endpoints for managing Trout Area fishing competitions,
which use head-to-head match-based scoring with qualifier rounds and
knockout brackets.

Key features:
- Event settings configuration
- Lineup generation with multiple pairing algorithms
- Match management and scoring
- Game card submission and validation (self-validation between competitors)
- Knockout bracket generation
- Real-time ranking updates
"""

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional


def sanitize_filename(name: str) -> str:
    """Sanitize event name for use in filename."""
    # Replace spaces with underscores
    name = name.replace(" ", "_")
    # Remove special characters except underscores and hyphens
    name = re.sub(r"[^a-zA-Z0-9_\-]", "", name)
    # Truncate to reasonable length
    return name[:50]

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.core.permissions import OrganizerOrAdmin, EventOwnerOrAdmin
from app.core.i18n import get_error_message
from app.core.exceptions import NotFoundError, ValidationError, ConflictError

from app.models.user import UserAccount, UserProfile
from app.models.event import Event, EventStatus
from app.models.enrollment import EventEnrollment
from app.models.trout_area import (
    TAPointsRule,
    TAEventPointConfig,
    TAEventSettings,
    TALineup,
    TAMatch,
    TAGameCard,
    TAKnockoutBracket,
    TAKnockoutMatch,
    TAQualifierStanding,
    TAMatchOutcome,
    TATournamentPhase,
    TAMatchStatus,
    TAGameCardStatus,
)

from app.schemas.trout_area import (
    # Points Rules
    TAPointsRuleResponse,
    # Event Point Config
    TAEventPointConfigResponse,
    TAEventPointConfigUpdate,
    # Settings
    TAEventSettingsCreate,
    TAEventSettingsUpdate,
    TAEventSettingsResponse,
    # Lineup
    TALineupResponse,
    TALineupListResponse,
    TAGenerateLineupRequest,
    TAGenerateLineupResponse,
    # Match
    TAMatchResponse,
    TAMatchDetailResponse,
    TAMatchListResponse,
    TAMatchResultUpdate,
    # Game Card
    TAGameCardResponse,
    TAGameCardSubmitRequest,
    TAGameCardValidateRequest,
    TAMyGameCardsResponse,
    # Bracket
    TAKnockoutBracketResponse,
    TABracketGenerateRequest,
    # Standings
    TAQualifierStandingResponse,
    TAQualifierStandingListResponse,
    # Rankings
    TARankingMovementResponse,
    TARankingUpdateResponse,
    # Duration
    TADurationEstimateRequest,
    TADurationEstimateResponse,
    # Algorithm Preview
    TAAlgorithmOption,
    TAAlgorithmPreviewResponse,
    # Schedule
    TARoundResponse,
    TAScheduleResponse,
    # Enums
    PairingAlgorithmAPI,
    TATournamentPhaseAPI,
    TAMatchOutcomeAPI,
    TAMatchStatusAPI,
    TAGameCardStatusAPI,
)

from app.schemas.common import MessageResponse
from app.services.ta_pairing import TAPairingService, PairingAlgorithm

router = APIRouter()


# =============================================================================
# Helper Functions
# =============================================================================

async def get_ta_event(
    event_id: int,
    db: AsyncSession,
    request: Request,
    require_settings: bool = True,
) -> Event:
    """Get event and verify it's a TA competition."""
    query = (
        select(Event)
        .options(selectinload(Event.event_type))
        .options(selectinload(Event.ta_settings))
        .where(Event.id == event_id, Event.is_deleted == False)
    )
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("ta_event_not_found", request),
        )

    # Verify event type is TA
    if event.event_type and event.event_type.code not in ["trout_area"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=get_error_message("ta_invalid_event_type", request),
        )

    if require_settings and not event.ta_settings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=get_error_message("ta_settings_not_found", request),
        )

    return event


def map_pairing_algorithm(api_algo: PairingAlgorithmAPI) -> PairingAlgorithm:
    """Map API enum to service enum."""
    mapping = {
        PairingAlgorithmAPI.ROUND_ROBIN_FULL: PairingAlgorithm.ROUND_ROBIN_FULL,
        PairingAlgorithmAPI.ROUND_ROBIN_HALF: PairingAlgorithm.ROUND_ROBIN_HALF,
        PairingAlgorithmAPI.ROUND_ROBIN_CUSTOM: PairingAlgorithm.ROUND_ROBIN_CUSTOM,
        PairingAlgorithmAPI.SIMPLE_PAIRS: PairingAlgorithm.SIMPLE_PAIRS,
    }
    return mapping[api_algo]


# =============================================================================
# Points Rules Endpoints
# =============================================================================

@router.get("/points-rules", response_model=list[TAPointsRuleResponse])
async def list_points_rules(
    db: AsyncSession = Depends(get_db),
) -> list[TAPointsRule]:
    """
    List all TA points rules.

    Returns the standard point values for match outcomes:
    - V (Victory): 3.0 points
    - T (Tie with fish): 1.5 points
    - T0 (Tie no fish): 1.0 points
    - L (Loss with fish): 0.5 points
    - L0 (Loss no fish): 0.0 points
    """
    query = (
        select(TAPointsRule)
        .where(TAPointsRule.is_active == True)
        .order_by(TAPointsRule.points.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


# =============================================================================
# Event Point Config Endpoints (Per-event customizable point values)
# =============================================================================

@router.get("/events/{event_id}/point-config", response_model=TAEventPointConfigResponse)
async def get_ta_point_config(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get point configuration for a TA event.

    Returns the event's custom point values if configured,
    or the default values (V=3.0, T=1.5, T0=1.0, L=0.5, L0=0.0).
    """
    # Verify event exists and is TA
    await get_ta_event(event_id, db, request, require_settings=False)

    # Get custom config if exists
    query = select(TAEventPointConfig).where(TAEventPointConfig.event_id == event_id)
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


@router.put("/events/{event_id}/point-config", response_model=TAEventPointConfigResponse)
async def update_ta_point_config(
    event_id: int,
    data: TAEventPointConfigUpdate,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Update point configuration for a TA event.

    Only the event organizer or administrator can modify point values.
    Creates a new config if one doesn't exist, or updates the existing one.

    Point values must follow logical ordering:
    victory >= tie >= tie_zero >= loss >= loss_zero
    """
    # Verify event exists and is TA
    await get_ta_event(event_id, db, request, require_settings=False)

    # Get or create config
    query = select(TAEventPointConfig).where(TAEventPointConfig.event_id == event_id)
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
        config = TAEventPointConfig(
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
async def reset_ta_point_config(
    event_id: int,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Reset point configuration to defaults by deleting custom config.

    After reset, the event will use the global default point values.
    """
    # Verify event exists and is TA
    await get_ta_event(event_id, db, request, require_settings=False)

    # Delete custom config if exists
    query = select(TAEventPointConfig).where(TAEventPointConfig.event_id == event_id)
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
    response_model=TAEventSettingsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_event_settings(
    event_id: int,
    data: TAEventSettingsCreate,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> TAEventSettings:
    """
    Create TA settings for an event.

    Only the event owner or administrator can configure TA settings.
    Settings must be created before generating lineups.
    """
    # Get event without requiring existing settings
    event = await get_ta_event(event_id, db, request, require_settings=False)

    # Check if settings already exist
    if event.ta_settings:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="TA settings already exist for this event. Use PATCH to update.",
        )

    settings = TAEventSettings(
        event_id=event_id,
        match_duration_minutes=data.match_duration_minutes,
        number_of_legs=data.legs_per_match,
        max_rounds_per_leg=data.matches_per_leg or 1,
        knockout_qualifiers=data.qualification_top_n,
        requalification_slots=data.requalification_spots,
        has_requalification=data.enable_requalification,
        is_team_event=data.enable_team_scoring,
        team_size=data.team_size,
        additional_rules=data.additional_rules or {},
    )

    db.add(settings)
    await db.commit()
    await db.refresh(settings)

    return settings


@router.get("/events/{event_id}/settings", response_model=TAEventSettingsResponse)
async def get_event_settings(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TAEventSettings:
    """Get TA settings for an event."""
    event = await get_ta_event(event_id, db, request)
    return event.ta_settings


@router.patch("/events/{event_id}/settings", response_model=TAEventSettingsResponse)
async def update_event_settings(
    event_id: int,
    data: TAEventSettingsUpdate,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> TAEventSettings:
    """
    Update TA settings for an event.

    Note: Some settings cannot be changed after lineups are generated.
    """
    event = await get_ta_event(event_id, db, request)
    settings = event.ta_settings

    update_data = data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        if hasattr(settings, field):
            if field == "pairing_algorithm" and value:
                setattr(settings, field, value.value if hasattr(value, "value") else value)
            else:
                setattr(settings, field, value)

    settings.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(settings)

    return settings


# =============================================================================
# Schedule Endpoints (for mobile app)
# =============================================================================

@router.get("/events/{event_id}/schedule", response_model=TAScheduleResponse)
async def get_event_schedule(
    event_id: int,
    phase: Optional[TATournamentPhaseAPI] = None,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get TA event schedule with all legs (manse) and matches.

    This endpoint provides a structured view of the competition schedule,
    organized by legs with match details.
    """
    event = await get_ta_event(event_id, db, request)
    settings = event.ta_settings

    # Build match query
    match_query = (
        select(TAMatch)
        .options(
            selectinload(TAMatch.competitor_a).selectinload(UserAccount.profile),
            selectinload(TAMatch.competitor_b).selectinload(UserAccount.profile),
        )
        .where(TAMatch.event_id == event_id)
        .order_by(TAMatch.round_number, TAMatch.match_number)
    )

    if phase:
        match_query = match_query.where(TAMatch.phase == phase.value)

    result = await db.execute(match_query)
    matches = result.scalars().all()

    if not matches:
        return {
            "legs": [],
            "current_leg": None,
            "total_legs": 0,
            "matches_completed": 0,
            "total_matches": 0,
            # Backwards compatibility
            "rounds": [],
            "current_round": None,
            "total_rounds": 0,
        }

    # Group matches by leg (round_number in DB = leg)
    legs_dict: dict[int, list] = {}
    total_completed = 0
    current_leg_num = None

    for match in matches:
        leg_num = match.round_number  # round_number in model = leg
        if leg_num not in legs_dict:
            legs_dict[leg_num] = []

        # Build match response with player names
        match_data = TAMatchResponse(
            id=match.id,
            event_id=match.event_id,
            leg_number=match.round_number,  # Map to leg_number for API
            match_number=match.match_number,
            phase=TATournamentPhaseAPI(match.phase) if match.phase else TATournamentPhaseAPI.QUALIFIER,
            player_a_id=match.competitor_a_id,
            player_b_id=match.competitor_b_id,
            seat_a=match.seat_a,
            seat_b=match.seat_b,
            player_a_catches=match.competitor_a_catches or 0,
            player_b_catches=match.competitor_b_catches or 0,
            player_a_points=match.competitor_a_points or 0,
            player_b_points=match.competitor_b_points or 0,
            player_a_outcome=TAMatchOutcomeAPI(match.competitor_a_outcome_code) if match.competitor_a_outcome_code else None,
            player_b_outcome=TAMatchOutcomeAPI(match.competitor_b_outcome_code) if match.competitor_b_outcome_code else None,
            status=TAMatchStatusAPI(match.status) if match.status else TAMatchStatusAPI.PENDING,
            started_at=match.started_at,
            completed_at=match.completed_at,
            created_at=match.created_at,
            player_a_name=match.competitor_a.profile.full_name if match.competitor_a and match.competitor_a.profile else None,
            player_b_name=match.competitor_b.profile.full_name if match.competitor_b and match.competitor_b.profile else None,
            player_a_avatar=match.competitor_a.avatar_url if match.competitor_a else None,
            player_b_avatar=match.competitor_b.avatar_url if match.competitor_b else None,
        )
        legs_dict[leg_num].append(match_data)

        if match.status == TAMatchStatus.COMPLETED:
            total_completed += 1
        elif match.status == TAMatchStatus.IN_PROGRESS:
            current_leg_num = leg_num

    # If no in-progress match, find the first incomplete leg
    if current_leg_num is None:
        for leg_num in sorted(legs_dict.keys()):
            leg_matches = legs_dict[leg_num]
            if any(m.status != TAMatchStatusAPI.COMPLETED for m in leg_matches):
                current_leg_num = leg_num
                break

    # Build legs response
    legs_list = []
    for leg_num in sorted(legs_dict.keys()):
        leg_matches = legs_dict[leg_num]
        completed_in_leg = sum(1 for m in leg_matches if m.status == TAMatchStatusAPI.COMPLETED)
        is_completed = completed_in_leg == len(leg_matches)
        legs_list.append(TARoundResponse(
            leg_number=leg_num,
            phase=leg_matches[0].phase if leg_matches else TATournamentPhaseAPI.QUALIFIER,
            matches=leg_matches,
            matches_completed=completed_in_leg,
            total_matches=len(leg_matches),
            is_current=(leg_num == current_leg_num),
            is_completed=is_completed,
        ))

    return {
        "legs": legs_list,
        "current_leg": current_leg_num,
        "total_legs": len(legs_list),
        "matches_completed": total_completed,
        "total_matches": len(matches),
        # Backwards compatibility aliases
        "rounds": legs_list,
        "current_round": current_leg_num,
        "total_rounds": len(legs_list),
    }


@router.get("/events/{event_id}/my-match", response_model=TAMatchResponse)
async def get_my_current_match(
    event_id: int,
    request: Request,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TAMatch:
    """
    Get the current user's active match in a TA event.

    Returns the user's current in-progress or pending match.
    Returns 404 if no active match found.
    """
    event = await get_ta_event(event_id, db, request)

    # Find user's active match (in_progress or pending)
    match_query = (
        select(TAMatch)
        .options(
            selectinload(TAMatch.competitor_a).selectinload(UserAccount.profile),
            selectinload(TAMatch.competitor_b).selectinload(UserAccount.profile),
        )
        .where(
            TAMatch.event_id == event_id,
            TAMatch.status.in_([TAMatchStatus.IN_PROGRESS.value, TAMatchStatus.SCHEDULED.value]),
            (TAMatch.competitor_a_id == current_user.id) | (TAMatch.competitor_b_id == current_user.id),
        )
        .order_by(TAMatch.round_number, TAMatch.match_number)
        .limit(1)
    )

    result = await db.execute(match_query)
    match = result.scalar_one_or_none()

    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active match found",
        )

    # Return match with player info
    return TAMatchResponse(
        id=match.id,
        event_id=match.event_id,
        leg_number=match.round_number,  # Map to leg_number for API
        match_number=match.match_number,
        phase=TATournamentPhaseAPI(match.phase) if match.phase else TATournamentPhaseAPI.QUALIFIER,
        player_a_id=match.competitor_a_id,
        player_b_id=match.competitor_b_id,
        seat_a=match.seat_a,
        seat_b=match.seat_b,
        player_a_catches=match.competitor_a_catches or 0,
        player_b_catches=match.competitor_b_catches or 0,
        player_a_points=match.competitor_a_points or 0,
        player_b_points=match.competitor_b_points or 0,
        player_a_outcome=TAMatchOutcomeAPI(match.competitor_a_outcome_code) if match.competitor_a_outcome_code else None,
        player_b_outcome=TAMatchOutcomeAPI(match.competitor_b_outcome_code) if match.competitor_b_outcome_code else None,
        status=TAMatchStatusAPI(match.status) if match.status else TAMatchStatusAPI.PENDING,
        started_at=match.started_at,
        completed_at=match.completed_at,
        created_at=match.created_at,
        player_a_name=match.competitor_a.profile.full_name if match.competitor_a and match.competitor_a.profile else None,
        player_b_name=match.competitor_b.profile.full_name if match.competitor_b and match.competitor_b.profile else None,
        player_a_avatar=match.competitor_a.avatar_url if match.competitor_a else None,
        player_b_avatar=match.competitor_b.avatar_url if match.competitor_b else None,
    )


# =============================================================================
# Lineup Endpoints
# =============================================================================

@router.get("/events/{event_id}/lineups", response_model=TALineupListResponse)
async def list_lineups(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get all lineups for a TA event."""
    event = await get_ta_event(event_id, db, request)

    query = (
        select(TALineup)
        .options(selectinload(TALineup.user).selectinload(UserAccount.profile))
        .where(TALineup.event_id == event_id)
        .order_by(TALineup.draw_number)
    )
    result = await db.execute(query)
    lineups = result.scalars().all()

    # Build response with user info
    items = []
    has_ghost = False
    sectors = set()

    for lineup in lineups:
        if lineup.is_ghost:
            has_ghost = True

        sectors.add(lineup.sector)

        item = {
            "id": lineup.id,
            "event_id": lineup.event_id,
            "user_id": lineup.user_id,
            "enrollment_id": lineup.enrollment_id,
            "team_id": lineup.team_id,
            "draw_number": lineup.draw_number,
            "sector": lineup.sector,
            "initial_seat": lineup.initial_seat,
            "is_ghost": lineup.is_ghost,
            "created_at": lineup.created_at,
            "user_name": None,
            "user_avatar": None,
        }

        if lineup.user and lineup.user.profile:
            item["user_name"] = lineup.user.profile.full_name
            item["user_avatar"] = lineup.user.avatar_url

        items.append(item)

    return {
        "items": items,
        "total": len(items),
        "has_ghost": has_ghost,
        "sectors": len(sectors),
    }


@router.get("/events/{event_id}/algorithm-preview", response_model=TAAlgorithmPreviewResponse)
async def get_algorithm_preview(
    event_id: int,
    request: Request,
    match_duration_minutes: int = Query(default=15, ge=5, le=60),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Preview available algorithms before generating lineups.

    Shows for each algorithm:
    - Number of legs (rounds)
    - Matches per leg
    - Total matches
    - Estimated duration
    - Warnings (e.g., "Very long for 40+ participants")

    Helps organizer choose the best algorithm for their event.
    """
    event = await get_ta_event(event_id, db, request, require_settings=False)

    # Get enrolled count
    enrolled_query = select(func.count()).select_from(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.status == "approved",
    )
    enrolled_result = await db.execute(enrolled_query)
    enrolled_count = enrolled_result.scalar() or 0

    if enrolled_count < 2:
        return {
            "event_id": event_id,
            "enrolled_count": enrolled_count,
            "effective_participants": 0,
            "has_ghost": False,
            "options": [],
            "recommended_algorithm": PairingAlgorithmAPI.ROUND_ROBIN_HALF,
        }

    # Calculate effective participants (add ghost if odd)
    has_ghost = enrolled_count % 2 == 1
    effective_participants = enrolled_count + (1 if has_ghost else 0)
    matches_per_leg = effective_participants // 2

    # Build algorithm options
    options = []

    # Half Round Robin (N/2 legs) - Recommended for most cases
    half_legs = effective_participants // 2
    half_total_matches = half_legs * matches_per_leg
    half_duration = TAPairingService.calculate_event_duration(
        num_participants=enrolled_count,
        algorithm=PairingAlgorithm.ROUND_ROBIN_HALF,
        match_duration_minutes=match_duration_minutes,
    )
    options.append(TAAlgorithmOption(
        algorithm=PairingAlgorithmAPI.ROUND_ROBIN_HALF,
        name="Half Round Robin",
        description="Each competitor plays half the field",
        legs=half_legs,
        matches_per_leg=matches_per_leg,
        total_matches=half_total_matches,
        estimated_duration_formatted=half_duration["total_duration_formatted"],
        is_recommended=enrolled_count <= 30,
        warning=None if enrolled_count <= 30 else "May be long for large groups",
    ))

    # Full Round Robin (N-1 legs)
    full_legs = effective_participants - 1
    full_total_matches = full_legs * matches_per_leg
    full_duration = TAPairingService.calculate_event_duration(
        num_participants=enrolled_count,
        algorithm=PairingAlgorithm.ROUND_ROBIN_FULL,
        match_duration_minutes=match_duration_minutes,
    )
    options.append(TAAlgorithmOption(
        algorithm=PairingAlgorithmAPI.ROUND_ROBIN_FULL,
        name="Full Round Robin",
        description="Everyone plays everyone",
        legs=full_legs,
        matches_per_leg=matches_per_leg,
        total_matches=full_total_matches,
        estimated_duration_formatted=full_duration["total_duration_formatted"],
        is_recommended=enrolled_count <= 12,
        warning="Very long for more than 12 participants" if enrolled_count > 12 else None,
    ))

    # Quarter Round Robin (N/4 legs) - for large groups
    quarter_legs = max(effective_participants // 4, 1)
    quarter_total_matches = quarter_legs * matches_per_leg
    quarter_duration = TAPairingService.calculate_event_duration(
        num_participants=enrolled_count,
        algorithm=PairingAlgorithm.ROUND_ROBIN_CUSTOM,
        match_duration_minutes=match_duration_minutes,
        custom_rounds=quarter_legs,
    )
    options.append(TAAlgorithmOption(
        algorithm=PairingAlgorithmAPI.ROUND_ROBIN_CUSTOM,
        name="Quarter Round Robin",
        description="Shorter tournament, N/4 legs",
        legs=quarter_legs,
        matches_per_leg=matches_per_leg,
        total_matches=quarter_total_matches,
        estimated_duration_formatted=quarter_duration["total_duration_formatted"],
        is_recommended=enrolled_count > 30,
        warning=None,
    ))

    # Simple Pairs (single leg)
    simple_duration = TAPairingService.calculate_event_duration(
        num_participants=enrolled_count,
        algorithm=PairingAlgorithm.SIMPLE_PAIRS,
        match_duration_minutes=match_duration_minutes,
    )
    options.append(TAAlgorithmOption(
        algorithm=PairingAlgorithmAPI.SIMPLE_PAIRS,
        name="Simple Pairs",
        description="Single round, quick tournament",
        legs=1,
        matches_per_leg=matches_per_leg,
        total_matches=matches_per_leg,
        estimated_duration_formatted=simple_duration["total_duration_formatted"],
        is_recommended=False,
        warning="Only one match per competitor",
    ))

    # Determine recommended algorithm
    if enrolled_count <= 12:
        recommended = PairingAlgorithmAPI.ROUND_ROBIN_FULL
    elif enrolled_count <= 30:
        recommended = PairingAlgorithmAPI.ROUND_ROBIN_HALF
    else:
        recommended = PairingAlgorithmAPI.ROUND_ROBIN_CUSTOM

    return {
        "event_id": event_id,
        "enrolled_count": enrolled_count,
        "effective_participants": effective_participants,
        "has_ghost": has_ghost,
        "options": options,
        "recommended_algorithm": recommended,
    }


@router.post(
    "/events/{event_id}/lineups/generate",
    response_model=TAGenerateLineupResponse,
)
async def generate_lineups(
    event_id: int,
    data: TAGenerateLineupRequest,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Generate lineups for a TA event using the specified pairing algorithm.

    This creates:
    1. Lineup entries with draw numbers, sectors, and initial seats
    2. Match schedule based on the pairing algorithm
    3. Game cards for each match

    Supports:
    - round_robin_full: Everyone plays everyone (N-1 rounds)
    - round_robin_half: Everyone plays half the field (N/2 rounds)
    - round_robin_custom: Specify exact number of rounds
    - simple_pairs: Single round n/2 pairing

    If participant count is odd, a ghost participant is added automatically.
    """
    event = await get_ta_event(event_id, db, request)
    settings = event.ta_settings

    # Delete existing lineups, matches, and game cards if regenerating
    # First delete game cards (depends on matches)
    await db.execute(
        TAGameCard.__table__.delete().where(TAGameCard.event_id == event_id)
    )
    # Delete standings
    await db.execute(
        TAQualifierStanding.__table__.delete().where(TAQualifierStanding.event_id == event_id)
    )
    # Delete matches
    await db.execute(
        TAMatch.__table__.delete().where(TAMatch.event_id == event_id)
    )
    # Delete lineups
    await db.execute(
        TALineup.__table__.delete().where(TALineup.event_id == event_id)
    )

    # Get enrolled participants
    enrollments_query = (
        select(EventEnrollment)
        .options(selectinload(EventEnrollment.user).selectinload(UserAccount.profile))
        .where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.status == "approved",
        )
        .order_by(EventEnrollment.draw_number.nullslast(), EventEnrollment.id)
    )
    enrollments_result = await db.execute(enrollments_query)
    enrollments = enrollments_result.scalars().all()

    if len(enrollments) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=get_error_message("ta_not_enough_participants", request, min=2),
        )

    # Prepare participants for pairing service
    participants = []
    for enrollment in enrollments:
        name = f"Player {enrollment.user_id}"
        if enrollment.user and enrollment.user.profile:
            profile = enrollment.user.profile
            name = f"{profile.first_name} {profile.last_name}".strip() or name

        participants.append({
            "user_id": enrollment.user_id,
            "enrollment_id": enrollment.id,
            "name": name,
        })

    # ========================================================================
    # Use Django-style TA rotation algorithm for lineup generation
    # ========================================================================
    import random

    # 1. Shuffle participants and assign draw numbers
    random.shuffle(participants)

    # Add ghost if odd number
    N = len(participants)
    has_ghost = N % 2 == 1
    if has_ghost:
        participants.append({
            "user_id": None,
            "enrollment_id": None,
            "name": "GHOST",
            "is_ghost": True,
        })
        N += 1

    # Mark non-ghost participants
    for p in participants:
        if "is_ghost" not in p:
            p["is_ghost"] = False

    # Assign draw numbers (1 to N)
    for i, p in enumerate(participants, start=1):
        p["draw_number"] = i

    # Update enrollments with draw numbers
    for p in participants:
        if not p["is_ghost"] and p["enrollment_id"]:
            for enrollment in enrollments:
                if enrollment.id == p["enrollment_id"]:
                    enrollment.draw_number = p["draw_number"]
                    break

    # 2. Calculate number of legs based on algorithm
    algorithm = map_pairing_algorithm(data.algorithm)
    if algorithm == PairingAlgorithm.ROUND_ROBIN_FULL:
        total_legs = N - 1
    elif algorithm == PairingAlgorithm.ROUND_ROBIN_HALF:
        total_legs = N // 2
    elif algorithm == PairingAlgorithm.ROUND_ROBIN_CUSTOM:
        total_legs = min(data.custom_legs or 1, N - 1)
    else:  # SIMPLE_PAIRS
        total_legs = 1

    matches_per_leg = N // 2

    # 3. Initialize seat rotation: sector_draw[seat-1] = draw_number at that seat
    sector_draw = list(range(1, N + 1))

    # 4. Determine special leg (one-time +4 for even seats)
    special_leg = None
    if total_legs % 2 == 0:
        special_leg = (total_legs // 2) + 1

    # 5. Create lineups and matches for each leg
    created_lineups = []

    for leg in range(1, total_legs + 1):
        # Create lineup entries for this leg
        for seat in range(1, N + 1):
            draw_num = sector_draw[seat - 1]
            participant = participants[draw_num - 1]

            lineup = TALineup(
                event_id=event_id,
                leg_number=leg,
                user_id=participant["user_id"],
                enrollment_id=participant["enrollment_id"],
                draw_number=draw_num,
                sector=1,
                seat_number=seat,
                is_ghost=participant["is_ghost"],
            )
            db.add(lineup)
            created_lineups.append(lineup)

        # Create matches by pairing adjacent seats: (1,2), (3,4), etc.
        match_num = 1
        for i in range(0, N, 2):
            seat_a = i + 1
            seat_b = i + 2

            draw_a = sector_draw[seat_a - 1]
            draw_b = sector_draw[seat_b - 1]

            p_a = participants[draw_a - 1]
            p_b = participants[draw_b - 1]

            is_ghost_match = p_a["is_ghost"] or p_b["is_ghost"]
            ghost_side = "A" if p_a["is_ghost"] else ("B" if p_b["is_ghost"] else None)

            ta_match = TAMatch(
                event_id=event_id,
                phase=TATournamentPhase.QUALIFIER.value,
                leg_number=leg,
                round_number=leg,
                match_number=match_num,
                competitor_a_id=p_a["user_id"],
                competitor_b_id=p_b["user_id"],
                seat_a=seat_a,
                seat_b=seat_b,
                is_ghost_match=is_ghost_match,
                ghost_side=ghost_side,
                status=TAMatchStatus.SCHEDULED.value,
            )
            db.add(ta_match)
            await db.flush()

            # Create game cards
            if not p_a["is_ghost"]:
                card_a = TAGameCard(
                    event_id=event_id,
                    match_id=ta_match.id,
                    leg_number=leg,
                    user_id=p_a["user_id"],
                    my_seat=seat_a,
                    opponent_id=p_b["user_id"],
                    opponent_seat=seat_b if not p_b["is_ghost"] else None,
                    is_ghost_opponent=p_b["is_ghost"],
                    status=TAGameCardStatus.DRAFT.value,
                )
                db.add(card_a)

            if not p_b["is_ghost"]:
                card_b = TAGameCard(
                    event_id=event_id,
                    match_id=ta_match.id,
                    leg_number=leg,
                    user_id=p_b["user_id"],
                    my_seat=seat_b,
                    opponent_id=p_a["user_id"],
                    opponent_seat=seat_a if not p_a["is_ghost"] else None,
                    is_ghost_opponent=p_a["is_ghost"],
                    status=TAGameCardStatus.DRAFT.value,
                )
                db.add(card_b)

            match_num += 1

        # 6. Rotate seats for next leg (unless last leg)
        if leg < total_legs:
            next_leg = leg + 1
            for seat in range(1, N + 1):
                old_draw = sector_draw[seat - 1]

                # Special leg: even seats get +4
                if special_leg and next_leg == special_leg and seat % 2 == 0:
                    new_draw = old_draw + 4
                    if new_draw > N:
                        new_draw -= N
                    sector_draw[seat - 1] = new_draw
                else:
                    # Normal rotation: odd seats -2, even seats +2
                    if seat % 2 == 1:
                        new_draw = old_draw - 2
                        if new_draw < 1:
                            new_draw += N
                        sector_draw[seat - 1] = new_draw
                    else:
                        new_draw = old_draw + 2
                        if new_draw > N:
                            new_draw -= N
                        sector_draw[seat - 1] = new_draw

    # Build pairing_result-like object for response
    real_participants = len(enrollments)
    total_matches = total_legs * matches_per_leg

    # Update settings with algorithm and draw info
    settings.pairing_algorithm = data.algorithm.value if hasattr(data.algorithm, 'value') else data.algorithm
    if data.custom_legs:
        settings.custom_legs = data.custom_legs
    settings.total_rounds = total_legs  # Keep model field name
    settings.matches_per_round = matches_per_leg  # Keep model field name
    settings.additional_rules = {
        **settings.additional_rules,
        "draw_completed": True,
        "total_legs": total_legs,
        "matches_per_leg": matches_per_leg,
        # Backwards compatibility
        "total_rounds": total_legs,
        "matches_per_round": matches_per_leg,
    }
    settings.updated_at = datetime.now(timezone.utc)

    await db.commit()

    # Calculate duration estimate
    duration = TAPairingService.calculate_event_duration(
        num_participants=len(participants),
        algorithm=algorithm,
        match_duration_minutes=settings.match_duration_minutes,
        custom_rounds=data.custom_legs,
    )

    # Refresh lineups for response
    for lineup in created_lineups:
        await db.refresh(lineup)

    # Build lineup response items
    lineup_items = []
    for lineup in created_lineups:
        item = TALineupResponse(
            id=lineup.id,
            event_id=lineup.event_id,
            user_id=lineup.user_id,
            enrollment_id=lineup.enrollment_id,
            team_id=lineup.team_id,
            draw_number=lineup.draw_number,
            sector=lineup.sector,
            initial_seat=lineup.initial_seat,
            is_ghost=lineup.is_ghost,
            created_at=lineup.created_at,
        )
        lineup_items.append(item)

    return {
        "message": get_error_message("lineup_created", request, count=real_participants),
        "total_participants": N,
        "real_participants": real_participants,
        "has_ghost": has_ghost,
        "algorithm": data.algorithm.value,
        "total_legs": total_legs,
        "matches_per_leg": matches_per_leg,
        "total_matches": total_matches,
        "estimated_duration": duration["total_duration_formatted"],
        "lineups": lineup_items,
    }


# =============================================================================
# Match Endpoints
# =============================================================================

@router.get("/events/{event_id}/matches", response_model=TAMatchListResponse)
async def list_matches(
    event_id: int,
    request: Request,
    phase: Optional[TATournamentPhaseAPI] = None,
    leg_number: Optional[int] = Query(None, alias="leg"),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    List matches for a TA event.

    Filters:
    - phase: Filter by tournament phase
    - leg: Filter by leg number (mansă)
    - status: Filter by match status
    """
    await get_ta_event(event_id, db, request)

    query = (
        select(TAMatch)
        .options(
            selectinload(TAMatch.competitor_a).selectinload(UserAccount.profile),
            selectinload(TAMatch.competitor_b).selectinload(UserAccount.profile),
        )
        .where(TAMatch.event_id == event_id)
    )

    if phase:
        query = query.where(TAMatch.phase == phase.value)
    if leg_number:
        query = query.where(TAMatch.round_number == leg_number)
    if status_filter:
        query = query.where(TAMatch.status == status_filter)

    query = query.order_by(TAMatch.round_number, TAMatch.match_number)

    result = await db.execute(query)
    matches = result.scalars().all()

    # Build response with user info
    items = []
    by_leg: dict[int, list] = {}

    for match in matches:
        # Get player names from loaded relationships
        player_a_name = None
        player_a_avatar = None
        if match.competitor_a and match.competitor_a.profile:
            player_a_name = match.competitor_a.profile.full_name
            player_a_avatar = match.competitor_a.avatar_url

        player_b_name = None
        player_b_avatar = None
        if match.competitor_b and match.competitor_b.profile:
            player_b_name = match.competitor_b.profile.full_name
            player_b_avatar = match.competitor_b.avatar_url

        item = TAMatchResponse(
            id=match.id,
            event_id=match.event_id,
            phase=TATournamentPhaseAPI(match.phase),
            leg_number=match.round_number,  # Map to leg_number for API
            match_number=match.match_number,
            player_a_id=match.competitor_a_id,
            player_b_id=match.competitor_b_id,
            seat_a=match.seat_a,
            seat_b=match.seat_b,
            player_a_catches=match.competitor_a_catches,
            player_b_catches=match.competitor_b_catches,
            player_a_points=match.competitor_a_points,
            player_b_points=match.competitor_b_points,
            player_a_outcome=TAMatchOutcome(match.competitor_a_outcome_code) if match.competitor_a_outcome_code else None,
            player_b_outcome=TAMatchOutcome(match.competitor_b_outcome_code) if match.competitor_b_outcome_code else None,
            status=match.status,
            started_at=match.started_at,
            completed_at=match.completed_at,
            created_at=match.created_at,
            player_a_name=player_a_name,
            player_b_name=player_b_name,
            player_a_avatar=player_a_avatar,
            player_b_avatar=player_b_avatar,
        )
        items.append(item)

        leg_num = match.round_number
        if leg_num not in by_leg:
            by_leg[leg_num] = []
        by_leg[leg_num].append(item)

    return {
        "items": items,
        "total": len(items),
        "by_leg": by_leg,
    }


@router.get("/events/{event_id}/matches/{match_id}", response_model=TAMatchDetailResponse)
async def get_match(
    event_id: int,
    match_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get detailed match information including game cards."""
    await get_ta_event(event_id, db, request)

    query = (
        select(TAMatch)
        .options(
            selectinload(TAMatch.game_cards).selectinload(TAGameCard.user).selectinload(UserAccount.profile),
            selectinload(TAMatch.game_cards).selectinload(TAGameCard.opponent).selectinload(UserAccount.profile),
        )
        .where(TAMatch.id == match_id, TAMatch.event_id == event_id)
    )
    result = await db.execute(query)
    match = result.scalar_one_or_none()

    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("ta_match_not_found", request),
        )

    # Build game cards response (new per-user, per-leg structure)
    game_cards = []
    for card in match.game_cards:
        game_cards.append(TAGameCardResponse(
            id=card.id,
            event_id=card.event_id,
            match_id=card.match_id,
            leg_number=card.leg_number,
            user_id=card.user_id,
            my_catches=card.my_catches,
            my_seat=card.my_seat,
            opponent_id=card.opponent_id,
            opponent_catches=card.opponent_catches,
            opponent_seat=card.opponent_seat,
            is_submitted=card.is_submitted,
            is_validated=card.is_validated,
            validated_at=card.validated_at,
            i_validated_opponent=card.i_validated_opponent,
            i_validated_at=card.i_validated_at,
            is_disputed=card.is_disputed,
            dispute_reason=card.dispute_reason,
            status=TAGameCardStatusAPI(card.status),
            is_ghost_opponent=card.is_ghost_opponent,
            submitted_at=card.submitted_at,
            created_at=card.created_at,
            updated_at=card.updated_at,
            user_name=card.user.profile.full_name if card.user and card.user.profile else None,
            user_avatar=card.user.avatar_url if card.user else None,
            opponent_name=card.opponent.profile.full_name if card.opponent and card.opponent.profile else None,
        ))

    return {
        "id": match.id,
        "event_id": match.event_id,
        "phase": TATournamentPhaseAPI(match.phase),
        "leg_number": match.round_number,  # Map to leg_number for API
        "match_number": match.match_number,
        "player_a_id": match.competitor_a_id,
        "player_b_id": match.competitor_b_id,
        "seat_a": match.seat_a,
        "seat_b": match.seat_b,
        "player_a_catches": match.competitor_a_catches or 0,
        "player_b_catches": match.competitor_b_catches or 0,
        "player_a_points": float(match.competitor_a_points) if match.competitor_a_points else 0,
        "player_b_points": float(match.competitor_b_points) if match.competitor_b_points else 0,
        "player_a_outcome": TAMatchOutcomeAPI(match.competitor_a_outcome_code) if match.competitor_a_outcome_code else None,
        "player_b_outcome": TAMatchOutcomeAPI(match.competitor_b_outcome_code) if match.competitor_b_outcome_code else None,
        "status": TAMatchStatusAPI(match.status),
        "started_at": match.started_at,
        "completed_at": match.completed_at,
        "created_at": match.created_at,
        "game_cards": game_cards,
    }


@router.post("/events/{event_id}/matches/{match_id}/start", response_model=TAMatchResponse)
async def start_match(
    event_id: int,
    match_id: int,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> TAMatch:
    """Start a scheduled match."""
    await get_ta_event(event_id, db, request)

    query = select(TAMatch).where(TAMatch.id == match_id, TAMatch.event_id == event_id)
    result = await db.execute(query)
    match = result.scalar_one_or_none()

    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("ta_match_not_found", request),
        )

    if match.status != TAMatchStatus.SCHEDULED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Match is not scheduled (current status: {match.status})",
        )

    match.status = TAMatchStatus.IN_PROGRESS.value
    match.started_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(match)

    return match


@router.patch("/events/{event_id}/matches/{match_id}/results", response_model=TAMatchResponse)
async def edit_match_results(
    event_id: int,
    match_id: int,
    data: TAMatchResultUpdate,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Edit match results (catches for competitors A and/or B).

    Only organizers and admins can edit match results.
    Edit history is tracked with edited_by_id, edited_at, and previous values.
    After editing, outcomes and points are recalculated using event's point config.
    """
    event = await get_ta_event(event_id, db, request)

    query = select(TAMatch).where(TAMatch.id == match_id, TAMatch.event_id == event_id)
    result = await db.execute(query)
    match = result.scalar_one_or_none()

    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("ta_match_not_found", request),
        )

    # Get point config for this event (custom or defaults)
    point_config_query = select(TAEventPointConfig).where(TAEventPointConfig.event_id == event_id)
    point_result = await db.execute(point_config_query)
    point_config = point_result.scalar_one_or_none()

    # Store previous values for audit
    update_data = data.model_dump(exclude_unset=True)
    if 'competitor_a_catches' in update_data:
        match.previous_a_catches = match.competitor_a_catches
    if 'competitor_b_catches' in update_data:
        match.previous_b_catches = match.competitor_b_catches

    # Track who edited and when
    match.edited_by_id = current_user.id
    match.edited_at = datetime.now(timezone.utc)

    # Apply updates
    for key, value in update_data.items():
        if hasattr(match, key):
            setattr(match, key, value)

    # Recalculate outcomes and points if both catches are set
    if match.competitor_a_catches is not None and match.competitor_b_catches is not None:
        match.calculate_outcome(point_config)  # This modifies the match object in place

    await db.commit()
    await db.refresh(match)

    # Build response with correct field mappings
    return {
        "id": match.id,
        "event_id": match.event_id,
        "phase": TATournamentPhaseAPI(match.phase),
        "leg_number": match.round_number,  # Map to leg_number for API
        "match_number": match.match_number,
        "player_a_id": match.competitor_a_id,
        "player_b_id": match.competitor_b_id,
        "seat_a": match.seat_a,
        "seat_b": match.seat_b,
        "player_a_catches": match.competitor_a_catches or 0,
        "player_b_catches": match.competitor_b_catches or 0,
        "player_a_points": match.competitor_a_points or Decimal("0.0"),
        "player_b_points": match.competitor_b_points or Decimal("0.0"),
        "player_a_outcome": TAMatchOutcomeAPI(match.competitor_a_outcome_code) if match.competitor_a_outcome_code else None,
        "player_b_outcome": TAMatchOutcomeAPI(match.competitor_b_outcome_code) if match.competitor_b_outcome_code else None,
        "status": TAMatchStatusAPI(match.status),
        "started_at": match.started_at,
        "completed_at": match.completed_at,
        "created_at": match.created_at,
    }


# =============================================================================
# Game Card Endpoints
# =============================================================================

@router.get("/events/{event_id}/game-cards/my", response_model=TAMyGameCardsResponse)
async def get_my_game_cards(
    event_id: int,
    request: Request,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get current user's game cards for a TA event (all legs)."""
    event = await get_ta_event(event_id, db, request)
    settings = event.ta_settings

    query = (
        select(TAGameCard)
        .options(
            selectinload(TAGameCard.user).selectinload(UserAccount.profile),
            selectinload(TAGameCard.opponent).selectinload(UserAccount.profile),
        )
        .where(
            TAGameCard.event_id == event_id,
            TAGameCard.user_id == current_user.id,
        )
        .order_by(TAGameCard.leg_number)
    )
    result = await db.execute(query)
    cards = result.scalars().all()

    # Determine current leg (first non-validated leg)
    current_leg = None
    for card in cards:
        if not card.is_validated:
            current_leg = card.leg_number
            break

    items = []
    for card in cards:
        items.append(TAGameCardResponse(
            id=card.id,
            event_id=card.event_id,
            match_id=card.match_id,
            leg_number=card.leg_number,
            user_id=card.user_id,
            my_catches=card.my_catches,
            my_seat=card.my_seat,
            opponent_id=card.opponent_id,
            opponent_catches=card.opponent_catches,
            opponent_seat=card.opponent_seat,
            is_submitted=card.is_submitted,
            is_validated=card.is_validated,
            validated_at=card.validated_at,
            i_validated_opponent=card.i_validated_opponent,
            i_validated_at=card.i_validated_at,
            is_disputed=card.is_disputed,
            dispute_reason=card.dispute_reason,
            status=TAGameCardStatusAPI(card.status),
            is_ghost_opponent=card.is_ghost_opponent,
            submitted_at=card.submitted_at,
            created_at=card.created_at,
            updated_at=card.updated_at,
            user_name=card.user.profile.full_name if card.user and card.user.profile else None,
            user_avatar=card.user.avatar_url if card.user else None,
            opponent_name=card.opponent.profile.full_name if card.opponent and card.opponent.profile else None,
        ))

    return {
        "items": items,
        "total": len(items),
        "current_leg": current_leg,
        "event_id": event_id,
    }


@router.post(
    "/events/{event_id}/game-cards/{card_id}/submit",
    response_model=TAGameCardResponse,
)
async def submit_game_card(
    event_id: int,
    card_id: int,
    data: TAGameCardSubmitRequest,
    request: Request,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Submit a game card with catches count.

    Self-validation flow:
    1. User enters their catches
    2. Card is marked as submitted
    3. If opponent already submitted, both see each other's catches
    4. If ghost opponent, auto-validates immediately

    The card can only be submitted by its owner.
    """
    await get_ta_event(event_id, db, request)

    # Get game card
    query = (
        select(TAGameCard)
        .options(
            selectinload(TAGameCard.user).selectinload(UserAccount.profile),
            selectinload(TAGameCard.opponent).selectinload(UserAccount.profile),
        )
        .where(
            TAGameCard.id == card_id,
            TAGameCard.event_id == event_id,
        )
    )
    result = await db.execute(query)
    card = result.scalar_one_or_none()

    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("ta_game_card_not_found", request),
        )

    # Verify ownership
    if card.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=get_error_message("ta_participant_not_in_match", request),
        )

    # Check status
    if card.status not in [TAGameCardStatus.DRAFT.value, TAGameCardStatus.DISPUTED.value]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=get_error_message("ta_game_card_locked", request),
        )

    # Update card with catches
    card.my_catches = data.my_catches
    card.is_submitted = True
    card.status = TAGameCardStatus.SUBMITTED.value
    card.submitted_at = datetime.now(timezone.utc)
    card.updated_at = datetime.now(timezone.utc)

    # If ghost opponent, auto-validate (both directions)
    if card.is_ghost_opponent:
        card.opponent_catches = 0
        # Ghost validates my catches (auto)
        card.is_validated = True
        card.validated_at = datetime.now(timezone.utc)
        # I validate ghost's catches (auto - they have 0)
        card.i_validated_opponent = True
        card.i_validated_at = datetime.now(timezone.utc)
        card.status = TAGameCardStatus.VALIDATED.value

    # Check if opponent has already submitted - if so, update opponent_catches
    if card.opponent_id:
        opponent_card_query = select(TAGameCard).where(
            TAGameCard.event_id == event_id,
            TAGameCard.leg_number == card.leg_number,
            TAGameCard.user_id == card.opponent_id,
        )
        opponent_result = await db.execute(opponent_card_query)
        opponent_card = opponent_result.scalar_one_or_none()

        if opponent_card and opponent_card.is_submitted:
            # Both have submitted - update both cards with opponent catches
            card.opponent_catches = opponent_card.my_catches
            opponent_card.opponent_catches = card.my_catches
            opponent_card.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(card)

    return {
        "id": card.id,
        "event_id": card.event_id,
        "match_id": card.match_id,
        "leg_number": card.leg_number,
        "user_id": card.user_id,
        "my_catches": card.my_catches,
        "my_seat": card.my_seat,
        "opponent_id": card.opponent_id,
        "opponent_catches": card.opponent_catches,
        "opponent_seat": card.opponent_seat,
        "is_submitted": card.is_submitted,
        "is_validated": card.is_validated,
        "validated_at": card.validated_at,
        "i_validated_opponent": card.i_validated_opponent,
        "i_validated_at": card.i_validated_at,
        "is_disputed": card.is_disputed,
        "dispute_reason": card.dispute_reason,
        "status": TAGameCardStatusAPI(card.status),
        "is_ghost_opponent": card.is_ghost_opponent,
        "submitted_at": card.submitted_at,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
        "user_name": card.user.profile.full_name if card.user and card.user.profile else None,
        "user_avatar": card.user.avatar_url if card.user else None,
        "opponent_name": card.opponent.profile.full_name if card.opponent and card.opponent.profile else None,
    }


@router.post(
    "/events/{event_id}/game-cards/{card_id}/validate",
    response_model=TAGameCardResponse,
)
async def validate_opponent_card(
    event_id: int,
    card_id: int,
    data: TAGameCardValidateRequest,
    request: Request,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Validate opponent's game card (self-validation in TA).

    Self-validation flow:
    1. Opponent submits their catches
    2. This user validates opponent's card
    3. If valid, card is marked validated
    4. If disputed, admin must resolve
    5. When both cards in a match are validated, match is complete

    This endpoint allows a participant to validate their opponent's card.
    """
    event = await get_ta_event(event_id, db, request)

    # Get the opponent's game card (the one we're validating)
    query = (
        select(TAGameCard)
        .options(
            selectinload(TAGameCard.user).selectinload(UserAccount.profile),
            selectinload(TAGameCard.opponent).selectinload(UserAccount.profile),
            selectinload(TAGameCard.match),
        )
        .where(
            TAGameCard.id == card_id,
            TAGameCard.event_id == event_id,
        )
    )
    result = await db.execute(query)
    opponent_card = result.scalar_one_or_none()

    if not opponent_card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("ta_game_card_not_found", request),
        )

    # Cannot validate own card
    if opponent_card.user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=get_error_message("ta_cannot_validate_own_card", request),
        )

    # Must be the opponent in the match
    if opponent_card.opponent_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=get_error_message("ta_participant_not_in_match", request),
        )

    # Card must be submitted
    if not opponent_card.is_submitted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Card must be submitted before validation",
        )

    # Already validated
    if opponent_card.is_validated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=get_error_message("ta_already_validated", request),
        )

    # Get my card to update i_validated_opponent
    my_card_query = select(TAGameCard).where(
        TAGameCard.event_id == event_id,
        TAGameCard.leg_number == opponent_card.leg_number,
        TAGameCard.user_id == current_user.id,
    )
    my_card_result = await db.execute(my_card_query)
    my_card = my_card_result.scalar_one_or_none()

    if data.is_valid:
        # Mark opponent's card as validated by me
        opponent_card.is_validated = True
        opponent_card.validated_by_id = current_user.id
        opponent_card.validated_at = datetime.now(timezone.utc)
        opponent_card.status = TAGameCardStatus.VALIDATED.value

        # Mark my card as having validated opponent
        if my_card:
            my_card.i_validated_opponent = True
            my_card.i_validated_at = datetime.now(timezone.utc)
            my_card.updated_at = datetime.now(timezone.utc)

        # Check if BOTH cards are now validated - if so, update match result
        if my_card and my_card.is_validated:
            # Both validated - update match results
            match = opponent_card.match
            if match:
                # Determine which side is which
                if match.competitor_a_id == opponent_card.user_id:
                    match.competitor_a_catches = opponent_card.my_catches
                    match.competitor_b_catches = my_card.my_catches
                else:
                    match.competitor_b_catches = opponent_card.my_catches
                    match.competitor_a_catches = my_card.my_catches

                # Calculate outcome
                point_config_query = select(TAEventPointConfig).where(TAEventPointConfig.event_id == event_id)
                point_result = await db.execute(point_config_query)
                point_config = point_result.scalar_one_or_none()
                match.calculate_outcome(point_config)

                match.status = TAMatchStatus.COMPLETED.value
                match.completed_at = datetime.now(timezone.utc)
    else:
        opponent_card.is_disputed = True
        opponent_card.dispute_reason = data.dispute_reason
        opponent_card.status = TAGameCardStatus.DISPUTED.value

    opponent_card.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(opponent_card)

    return {
        "id": opponent_card.id,
        "event_id": opponent_card.event_id,
        "match_id": opponent_card.match_id,
        "leg_number": opponent_card.leg_number,
        "user_id": opponent_card.user_id,
        "my_catches": opponent_card.my_catches,
        "my_seat": opponent_card.my_seat,
        "opponent_id": opponent_card.opponent_id,
        "opponent_catches": opponent_card.opponent_catches,
        "opponent_seat": opponent_card.opponent_seat,
        "is_submitted": opponent_card.is_submitted,
        "is_validated": opponent_card.is_validated,
        "validated_at": opponent_card.validated_at,
        "i_validated_opponent": opponent_card.i_validated_opponent,
        "i_validated_at": opponent_card.i_validated_at,
        "is_disputed": opponent_card.is_disputed,
        "dispute_reason": opponent_card.dispute_reason,
        "status": TAGameCardStatusAPI(opponent_card.status),
        "is_ghost_opponent": opponent_card.is_ghost_opponent,
        "submitted_at": opponent_card.submitted_at,
        "created_at": opponent_card.created_at,
        "updated_at": opponent_card.updated_at,
        "user_name": opponent_card.user.profile.full_name if opponent_card.user and opponent_card.user.profile else None,
        "user_avatar": opponent_card.user.avatar_url if opponent_card.user else None,
        "opponent_name": opponent_card.opponent.profile.full_name if opponent_card.opponent and opponent_card.opponent.profile else None,
    }


# =============================================================================
# Admin Game Card Endpoints (Validator/Organizer)
# =============================================================================

@router.get("/events/{event_id}/game-cards", response_model=TAMyGameCardsResponse)
async def get_user_game_cards(
    event_id: int,
    request: Request,
    user_id: Optional[int] = Query(None, description="User ID to fetch cards for"),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get game cards for a specific user (admin/validator access).

    If user_id is not provided, returns cards for current user.
    Requires validator/organizer/admin permissions to fetch other users' cards.
    """
    event = await get_ta_event(event_id, db, request)

    target_user_id = user_id if user_id else current_user.id

    # If fetching another user's cards, check permissions
    if target_user_id != current_user.id:
        # Check if current user is organizer, validator, or admin
        user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
        is_admin = "administrator" in user_roles
        is_organizer = "organizer" in user_roles
        is_event_owner = event.created_by_id == current_user.id

        if not (is_admin or is_organizer or is_event_owner):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view other users' game cards",
            )

    query = (
        select(TAGameCard)
        .options(
            selectinload(TAGameCard.user).selectinload(UserAccount.profile),
            selectinload(TAGameCard.opponent).selectinload(UserAccount.profile),
        )
        .where(
            TAGameCard.event_id == event_id,
            TAGameCard.user_id == target_user_id,
        )
        .order_by(TAGameCard.leg_number)
    )
    result = await db.execute(query)
    cards = result.scalars().all()

    # Determine current leg (first non-validated leg)
    current_leg = None
    for card in cards:
        if not card.is_validated:
            current_leg = card.leg_number
            break

    items = []
    for card in cards:
        items.append(TAGameCardResponse(
            id=card.id,
            event_id=card.event_id,
            match_id=card.match_id,
            leg_number=card.leg_number,
            user_id=card.user_id,
            my_catches=card.my_catches,
            my_seat=card.my_seat,
            opponent_id=card.opponent_id,
            opponent_catches=card.opponent_catches,
            opponent_seat=card.opponent_seat,
            is_submitted=card.is_submitted,
            is_validated=card.is_validated,
            validated_at=card.validated_at,
            i_validated_opponent=card.i_validated_opponent,
            i_validated_at=card.i_validated_at,
            is_disputed=card.is_disputed,
            dispute_reason=card.dispute_reason,
            status=TAGameCardStatusAPI(card.status),
            is_ghost_opponent=card.is_ghost_opponent,
            submitted_at=card.submitted_at,
            created_at=card.created_at,
            updated_at=card.updated_at,
            user_name=card.user.profile.full_name if card.user and card.user.profile else None,
            user_avatar=card.user.avatar_url if card.user else None,
            opponent_name=card.opponent.profile.full_name if card.opponent and card.opponent.profile else None,
        ))

    return {
        "items": items,
        "total": len(items),
        "current_leg": current_leg,
        "event_id": event_id,
    }


@router.patch(
    "/events/{event_id}/game-cards/{card_id}/admin-update",
    response_model=TAGameCardResponse,
)
async def admin_update_game_card(
    event_id: int,
    card_id: int,
    request: Request,
    my_catches: Optional[int] = None,
    is_submitted: Optional[bool] = None,
    is_validated: Optional[bool] = None,
    i_validated_opponent: Optional[bool] = None,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Admin/Validator update of a game card.

    Allows organizers and validators to manually update game card data
    for situations where users cannot submit themselves.
    """
    event = await get_ta_event(event_id, db, request)

    # Check permissions - must be organizer, validator, or admin
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_admin = "administrator" in user_roles
    is_organizer = "organizer" in user_roles
    is_validator = "validator" in user_roles
    is_event_owner = event.created_by_id == current_user.id

    if not (is_admin or is_organizer or is_validator or is_event_owner):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update game cards",
        )

    # Get the card
    query = (
        select(TAGameCard)
        .options(
            selectinload(TAGameCard.user).selectinload(UserAccount.profile),
            selectinload(TAGameCard.opponent).selectinload(UserAccount.profile),
            selectinload(TAGameCard.match),
        )
        .where(
            TAGameCard.id == card_id,
            TAGameCard.event_id == event_id,
        )
    )
    result = await db.execute(query)
    card = result.scalar_one_or_none()

    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=get_error_message("ta_game_card_not_found", request),
        )

    # Update fields
    if my_catches is not None:
        card.my_catches = my_catches
    if is_submitted is not None:
        card.is_submitted = is_submitted
        if is_submitted and not card.submitted_at:
            card.submitted_at = datetime.now(timezone.utc)
    if is_validated is not None:
        card.is_validated = is_validated
        if is_validated and not card.validated_at:
            card.validated_at = datetime.now(timezone.utc)
            card.validated_by_id = current_user.id
    if i_validated_opponent is not None:
        card.i_validated_opponent = i_validated_opponent
        if i_validated_opponent and not card.i_validated_at:
            card.i_validated_at = datetime.now(timezone.utc)

    card.updated_at = datetime.now(timezone.utc)

    # Update status based on new state
    if card.is_disputed:
        card.status = TAGameCardStatus.DISPUTED.value
    elif card.is_validated and card.i_validated_opponent:
        card.status = TAGameCardStatus.COMPLETED.value
    elif card.is_validated:
        card.status = TAGameCardStatus.VALIDATED.value
    elif card.is_submitted:
        card.status = TAGameCardStatus.SUBMITTED.value

    # Check if match should be completed
    if card.match and card.is_validated and card.i_validated_opponent:
        # Get opponent's card
        opp_card_query = select(TAGameCard).where(
            TAGameCard.match_id == card.match_id,
            TAGameCard.user_id == card.opponent_id,
        )
        opp_result = await db.execute(opp_card_query)
        opp_card = opp_result.scalar_one_or_none()

        if opp_card and opp_card.is_validated and opp_card.i_validated_opponent:
            # Both cards validated, update match
            match = card.match
            match.player_a_catches = card.my_catches if match.player_a_id == card.user_id else opp_card.my_catches
            match.player_b_catches = opp_card.my_catches if match.player_a_id == card.user_id else card.my_catches
            match.status = TAMatchStatus.COMPLETED.value
            match.completed_at = datetime.now(timezone.utc)

            # Calculate outcomes
            a_catches = match.player_a_catches or 0
            b_catches = match.player_b_catches or 0

            if a_catches > b_catches:
                match.player_a_outcome = TAMatchOutcome.VICTORY.value
                match.player_b_outcome = TAMatchOutcome.LOSS.value
            elif b_catches > a_catches:
                match.player_a_outcome = TAMatchOutcome.LOSS.value
                match.player_b_outcome = TAMatchOutcome.VICTORY.value
            else:
                if a_catches == 0:
                    match.player_a_outcome = TAMatchOutcome.TIE_ZERO.value
                    match.player_b_outcome = TAMatchOutcome.TIE_ZERO.value
                else:
                    match.player_a_outcome = TAMatchOutcome.TIE.value
                    match.player_b_outcome = TAMatchOutcome.TIE.value

    await db.commit()
    await db.refresh(card)

    return {
        "id": card.id,
        "event_id": card.event_id,
        "match_id": card.match_id,
        "leg_number": card.leg_number,
        "user_id": card.user_id,
        "my_catches": card.my_catches,
        "my_seat": card.my_seat,
        "opponent_id": card.opponent_id,
        "opponent_catches": card.opponent_catches,
        "opponent_seat": card.opponent_seat,
        "is_submitted": card.is_submitted,
        "is_validated": card.is_validated,
        "validated_at": card.validated_at,
        "i_validated_opponent": card.i_validated_opponent,
        "i_validated_at": card.i_validated_at,
        "is_disputed": card.is_disputed,
        "dispute_reason": card.dispute_reason,
        "status": TAGameCardStatusAPI(card.status),
        "is_ghost_opponent": card.is_ghost_opponent,
        "submitted_at": card.submitted_at,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
        "user_name": card.user.profile.full_name if card.user and card.user.profile else None,
        "user_avatar": card.user.avatar_url if card.user else None,
        "opponent_name": card.opponent.profile.full_name if card.opponent and card.opponent.profile else None,
    }


# =============================================================================
# Standings Endpoints
# =============================================================================

@router.post("/events/{event_id}/standings/recalculate", response_model=MessageResponse)
async def recalculate_standings(
    event_id: int,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Recalculate all standings for a TA event.

    This rebuilds the qualifier standings table from all completed matches.
    Useful after manual match edits or data corrections.
    """
    from app.services.ta_ranking import TARankingService

    event = await get_ta_event(event_id, db, request)

    # Get all completed matches
    matches_query = select(TAMatch).where(
        TAMatch.event_id == event_id,
        TAMatch.status == TAMatchStatus.COMPLETED.value,
    )
    result = await db.execute(matches_query)
    matches = result.scalars().all()

    # Clear existing standings
    await db.execute(
        TAQualifierStanding.__table__.delete().where(TAQualifierStanding.event_id == event_id)
    )

    # Rebuild standings from matches
    ranking_service = TARankingService(db)

    # Accumulate stats by user
    user_stats: dict[int, dict] = {}

    for match in matches:
        if match.competitor_a_id:
            if match.competitor_a_id not in user_stats:
                user_stats[match.competitor_a_id] = {
                    "total_points": Decimal("0"),
                    "total_catches": 0,
                    "matches_played": 0,
                    "victories": 0,
                    "ties": 0,
                    "losses": 0,
                }
            stats = user_stats[match.competitor_a_id]
            stats["total_points"] += match.competitor_a_points or Decimal("0")
            stats["total_catches"] += match.competitor_a_catches or 0
            stats["matches_played"] += 1
            if match.competitor_a_outcome_code == "V":
                stats["victories"] += 1
            elif match.competitor_a_outcome_code in ["T", "T0"]:
                stats["ties"] += 1
            else:
                stats["losses"] += 1

        if match.competitor_b_id:
            if match.competitor_b_id not in user_stats:
                user_stats[match.competitor_b_id] = {
                    "total_points": Decimal("0"),
                    "total_catches": 0,
                    "matches_played": 0,
                    "victories": 0,
                    "ties": 0,
                    "losses": 0,
                }
            stats = user_stats[match.competitor_b_id]
            stats["total_points"] += match.competitor_b_points or Decimal("0")
            stats["total_catches"] += match.competitor_b_catches or 0
            stats["matches_played"] += 1
            if match.competitor_b_outcome_code == "V":
                stats["victories"] += 1
            elif match.competitor_b_outcome_code in ["T", "T0"]:
                stats["ties"] += 1
            else:
                stats["losses"] += 1

    # Create standing records
    for user_id, stats in user_stats.items():
        standing = TAQualifierStanding(
            event_id=event_id,
            user_id=user_id,
            rank=0,  # Will be calculated
            total_points=stats["total_points"],
            total_catches=stats["total_catches"],
            total_length=0.0,
            matches_played=stats["matches_played"],
            victories=stats["victories"],
            ties=stats["ties"],
            losses=stats["losses"],
        )
        db.add(standing)

    await db.flush()

    # Recalculate ranks
    await ranking_service._recalculate_ranks(event_id)
    await db.commit()

    return {"message": f"Standings recalculated for {len(user_stats)} participants"}


@router.get(
    "/events/{event_id}/standings",
    response_model=TAQualifierStandingListResponse,
)
async def get_standings(
    event_id: int,
    request: Request,
    phase: Optional[TATournamentPhaseAPI] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get current standings for a TA event."""
    event = await get_ta_event(event_id, db, request)
    settings = event.ta_settings

    query = (
        select(TAQualifierStanding)
        .options(selectinload(TAQualifierStanding.user).selectinload(UserAccount.profile))
        .where(TAQualifierStanding.event_id == event_id)
        .order_by(TAQualifierStanding.rank)
    )
    result = await db.execute(query)
    standings = result.scalars().all()

    items = []
    for standing in standings:
        item = TAQualifierStandingResponse(
            id=standing.id,
            event_id=standing.event_id,
            user_id=standing.user_id,
            rank=standing.rank,
            total_points=standing.total_points,
            total_catches=standing.total_catches,
            total_length=standing.total_length,
            matches_played=standing.matches_played,
            victories=standing.victories,
            ties=standing.ties,
            losses=standing.losses,
            updated_at=standing.updated_at,
            user_name=standing.user.profile.full_name if standing.user and standing.user.profile else None,
            user_avatar=standing.user.avatar_url if standing.user else None,
        )
        items.append(item)

    current_phase = TATournamentPhaseAPI(
        settings.additional_rules.get("current_phase", "qualifier")
    )

    return {
        "items": items,
        "total": len(items),
        "phase": current_phase,
        "qualified_count": min(len(items), settings.knockout_qualifiers),
        "requalification_count": settings.requalification_slots if settings.has_requalification else 0,
    }


# =============================================================================
# Detailed Rankings Endpoints (Leg-by-Leg with Tiebreakers)
# =============================================================================

@router.get("/events/{event_id}/rankings/leg/{leg_number}")
async def get_leg_ranking(
    event_id: int,
    leg_number: int,
    request: Request,
    phase: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get cumulative ranking up to specified leg with detailed stats.

    Returns rankings with V/T/T0/L/L0 breakdown and proper tiebreaker sorting.
    """
    from app.services.ta_ranking import TARankingService
    from app.schemas.trout_area import TACompetitorDetailedStats, TALegRankingResponse

    event = await get_ta_event(event_id, db, request)
    settings = event.ta_settings

    ranking_service = TARankingService(db)
    rankings = await ranking_service.compute_leg_ranking(event_id, leg_number, phase)

    current_phase = TATournamentPhaseAPI(
        settings.additional_rules.get("current_phase", "qualifier") if settings else "qualifier"
    )

    return {
        "event_id": event_id,
        "leg_number": leg_number,
        "phase": current_phase,
        "is_cumulative": True,
        "rankings": rankings,
        "total_participants": len(rankings),
    }


@router.get("/events/{event_id}/rankings")
async def get_overall_ranking(
    event_id: int,
    request: Request,
    phase: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get overall ranking for the event (all legs cumulative).

    Returns rankings with V/T/T0/L/L0 breakdown and proper tiebreaker sorting.
    """
    from app.services.ta_ranking import TARankingService

    event = await get_ta_event(event_id, db, request)
    settings = event.ta_settings

    ranking_service = TARankingService(db)
    rankings = await ranking_service.compute_leg_ranking(event_id, phase=phase)

    current_phase = TATournamentPhaseAPI(
        settings.additional_rules.get("current_phase", "qualifier") if settings else "qualifier"
    )

    return {
        "event_id": event_id,
        "leg_number": None,
        "phase": current_phase,
        "is_cumulative": True,
        "rankings": rankings,
        "total_participants": len(rankings),
    }


@router.get("/events/{event_id}/matches/leg/{leg_number}")
async def get_leg_matches(
    event_id: int,
    leg_number: int,
    request: Request,
    phase: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get all matches for a specific leg with A vs B breakdown.

    Returns match details showing catches, outcomes, and points for both competitors.
    """
    from app.services.ta_ranking import TARankingService

    event = await get_ta_event(event_id, db, request)
    settings = event.ta_settings

    ranking_service = TARankingService(db)
    matches = await ranking_service.get_leg_matches(event_id, leg_number, phase)

    current_phase = TATournamentPhaseAPI(
        settings.additional_rules.get("current_phase", "qualifier") if settings else "qualifier"
    )

    return {
        "event_id": event_id,
        "leg_number": leg_number,
        "phase": current_phase,
        "matches": matches,
        "total_matches": len(matches),
    }


@router.get("/events/{event_id}/statistics")
async def get_event_statistics(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get event-level statistics for TA competition.

    Returns total participants, matches, catches, and top performers.
    """
    from app.services.ta_ranking import TARankingService

    event = await get_ta_event(event_id, db, request)

    ranking_service = TARankingService(db)
    stats = await ranking_service.get_event_statistics(event_id)

    return stats


@router.get("/events/{event_id}/standings/export")
async def export_standings_csv(
    event_id: int,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
):
    """
    Export TA standings to CSV.

    Includes:
    - QUALIFIER PHASE: Rankings from qualifier legs
    - FINAL RANKING: Overall standings with points, catches, wins/ties/losses
    """
    from fastapi.responses import StreamingResponse
    from io import StringIO
    import csv

    event = await get_ta_event(event_id, db, request, require_settings=False)
    event_name_safe = sanitize_filename(event.name) if event.name else f"Event_{event_id}"
    start_date_str = event.start_date.strftime("%d%m%Y") if event.start_date else "nodate"

    # Get rankings
    ranking_service = TARankingService(db)
    rankings = await ranking_service.compute_leg_ranking(event_id)

    # Create CSV content
    output = StringIO()
    writer = csv.writer(output)

    # === SECTION: Event Info ===
    writer.writerow(["TA Competition Results"])
    writer.writerow(["Event:", event.name or f"Event {event_id}"])
    writer.writerow(["Export Date:", datetime.now().strftime("%Y-%m-%d %H:%M")])
    writer.writerow([])

    # === SECTION: Qualifier Phase Rankings ===
    writer.writerow(["=== QUALIFIER PHASE RANKINGS ==="])
    writer.writerow([])

    # Header
    writer.writerow([
        "Rank", "Draw #", "Name",
        "Points", "Catches", "Victories", "Ties", "Losses",
        "Matches Played", "Win Rate %"
    ])

    # Data rows
    for idx, ranking in enumerate(rankings, 1):
        matches_played = ranking.get("victories", 0) + ranking.get("ties", 0) + ranking.get("losses", 0)
        win_rate = (ranking.get("victories", 0) / matches_played * 100) if matches_played > 0 else 0

        writer.writerow([
            idx,
            ranking.get("draw_number", ""),
            ranking.get("full_name", ranking.get("user_name", f"User {ranking.get('user_id', '')}")),
            ranking.get("total_points", 0),
            ranking.get("captures", ranking.get("total_catches", 0)),
            ranking.get("victories", 0),
            ranking.get("ties", 0),
            ranking.get("losses", 0),
            matches_played,
            f"{win_rate:.1f}",
        ])

    writer.writerow([])

    # === SECTION: Final Ranking (same as qualifier for now) ===
    writer.writerow(["=== FINAL RANKING ==="])
    writer.writerow([])
    writer.writerow([
        "Position", "Name", "Total Points", "Total Catches"
    ])

    for idx, ranking in enumerate(rankings, 1):
        writer.writerow([
            idx,
            ranking.get("full_name", ranking.get("user_name", f"User {ranking.get('user_id', '')}")),
            ranking.get("total_points", 0),
            ranking.get("captures", ranking.get("total_catches", 0)),
        ])

    # Return CSV file
    output.seek(0)
    filename = f"{event_name_safe}_{start_date_str}_TA_Standings.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/events/{event_id}/team-standings")
async def get_team_standings(
    event_id: int,
    request: Request,
    phase: Optional[str] = Query(None, description="Filter by phase (qualifier, semifinal, final)"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get team standings for TA team events.

    Returns team rankings with member breakdown.
    Only available for events with is_team_event=True.

    Scoring methods:
    - sum: Total of all member points
    - average: Average of member points
    - best_n: Sum of top N member scores

    Tiebreakers: points → captures → victories → ties_with_fish
    """
    from app.services.ta_ranking import TARankingService

    event = await get_ta_event(event_id, db, request)

    # Check if team event
    settings_query = select(TAEventSettings).where(TAEventSettings.event_id == event_id)
    settings_result = await db.execute(settings_query)
    settings = settings_result.scalar_one_or_none()

    if not settings or not settings.is_team_event:
        raise HTTPException(
            status_code=400,
            detail="Team standings only available for team events"
        )

    ranking_service = TARankingService(db)
    team_rankings = await ranking_service.compute_team_ranking(event_id, phase=phase)

    return {
        "items": team_rankings,
        "total": len(team_rankings),
        "scoring_method": settings.team_scoring_method or "sum",
        "team_size": settings.team_size,
    }


# =============================================================================
# Duration Estimate Endpoint
# =============================================================================

@router.get("/events/{event_id}/lineups/export")
async def export_lineups_excel(
    event_id: int,
    request: Request,
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
    db: AsyncSession = Depends(get_db),
):
    """
    Export TA lineups and game cards to Excel.

    Sheet 1: Lineup - Grid (rows=legs, cols=seats) with draw numbers
    Sheet 2: User Seat Rotation - Draw#, Username, then seat for each leg
    Sheet 3: Seat-Leg Pivot - Grid (rows=seats, cols=legs) with draw numbers
    """
    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook
    from openpyxl.styles import PatternFill, Font
    from io import BytesIO

    event = await get_ta_event(event_id, db, request, require_settings=False)

    # Get lineups
    lineup_query = (
        select(TALineup)
        .options(selectinload(TALineup.user).selectinload(UserAccount.profile))
        .where(TALineup.event_id == event_id)
        .order_by(TALineup.leg_number, TALineup.seat_number)
    )
    result = await db.execute(lineup_query)
    lineups = result.scalars().all()

    if not lineups:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No lineup data found for this event",
        )

    # Build lookup structures
    distinct_legs = sorted(set(l.leg_number for l in lineups))
    distinct_seats = sorted(set(l.seat_number for l in lineups if l.seat_number))

    # seat_draw[leg][seat] = draw_number
    # ghost_map[(leg, seat)] = is_ghost
    seat_draw: dict[int, dict[int, int]] = {}
    ghost_map: dict[tuple[int, int], bool] = {}

    # data_cards[draw_num] = { 'user_name': ..., 'legs': { leg: seat, ... } }
    data_cards: dict[int, dict] = {}

    for lineup in lineups:
        leg = lineup.leg_number
        seat = lineup.seat_number
        draw_num = lineup.draw_number

        # Build seat_draw grid
        if leg not in seat_draw:
            seat_draw[leg] = {}
        seat_draw[leg][seat] = draw_num

        # Track ghosts
        ghost_map[(leg, seat)] = lineup.is_ghost

        # Build user cards (skip ghosts)
        if not lineup.is_ghost and draw_num:
            if draw_num not in data_cards:
                user_name = (
                    f"{lineup.user.profile.last_name} {lineup.user.profile.first_name}".strip()
                    if lineup.user and lineup.user.profile else f"User {lineup.user_id}"
                )
                data_cards[draw_num] = {"user_name": user_name, "legs": {}}
            data_cards[draw_num]["legs"][leg] = seat

    # Create Excel workbook
    wb = Workbook()
    ghost_fill = PatternFill(fill_type='solid', start_color='90EE90', end_color='90EE90')
    bold_font = Font(bold=True)

    # ===================== SHEET #1: Lineup Grid (Legs x Seats) =====================
    ws_lineup = wb.active
    ws_lineup.title = "Lineup"

    # Header row: "Leg", then seats, then "Sum"
    ws_lineup.cell(row=1, column=1, value="Leg").font = bold_font
    for col_idx, seat_num in enumerate(distinct_seats, start=2):
        ws_lineup.cell(row=1, column=col_idx, value=f"#S{seat_num:02d}").font = bold_font
    sum_col = len(distinct_seats) + 2
    ws_lineup.cell(row=1, column=sum_col, value="Sum").font = bold_font

    # Data rows: one per leg, with row sum
    col_sums = {seat: 0 for seat in distinct_seats}
    for row_idx, leg_num in enumerate(distinct_legs, start=2):
        ws_lineup.cell(row=row_idx, column=1, value=leg_num)
        row_sum = 0
        for col_idx, seat_num in enumerate(distinct_seats, start=2):
            draw_val = seat_draw.get(leg_num, {}).get(seat_num, "")
            cell = ws_lineup.cell(row=row_idx, column=col_idx, value=draw_val)
            if ghost_map.get((leg_num, seat_num), False):
                cell.fill = ghost_fill
            # Add to sums (only if numeric)
            if isinstance(draw_val, (int, float)):
                row_sum += draw_val
                col_sums[seat_num] += draw_val
        # Row sum cell
        sum_cell = ws_lineup.cell(row=row_idx, column=sum_col, value=row_sum)
        sum_cell.font = bold_font

    # Column sums row at the bottom
    sum_row = len(distinct_legs) + 2
    ws_lineup.cell(row=sum_row, column=1, value="Sum").font = bold_font
    for col_idx, seat_num in enumerate(distinct_seats, start=2):
        sum_cell = ws_lineup.cell(row=sum_row, column=col_idx, value=col_sums[seat_num])
        sum_cell.font = bold_font

    # ===================== SHEET #2: User Seat Rotation =====================
    ws_rotation = wb.create_sheet(title="User Seat Rotation")

    # Header: Draw#, Username, then Leg X Seat columns
    ws_rotation.cell(row=1, column=1, value="Draw #").font = bold_font
    ws_rotation.cell(row=1, column=2, value="Username").font = bold_font
    for col_idx, leg_num in enumerate(distinct_legs, start=3):
        ws_rotation.cell(row=1, column=col_idx, value=f"Leg {leg_num} Seat").font = bold_font

    # Data rows: one per user (sorted by draw number)
    sorted_draw_numbers = sorted(data_cards.keys())
    for row_idx, draw_num in enumerate(sorted_draw_numbers, start=2):
        info = data_cards[draw_num]
        ws_rotation.cell(row=row_idx, column=1, value=draw_num)
        ws_rotation.cell(row=row_idx, column=2, value=info["user_name"])

        for col_idx, leg_num in enumerate(distinct_legs, start=3):
            seat_val = info["legs"].get(leg_num, "")
            ws_rotation.cell(row=row_idx, column=col_idx, value=seat_val)

    # ===================== SHEET #3: Seat-Leg Pivot (Seats x Legs) =====================
    ws_pivot = wb.create_sheet(title="Seat-Leg Pivot")

    # Header: "Seat", then Leg columns, then "Sum"
    ws_pivot.cell(row=1, column=1, value="Seat").font = bold_font
    for col_idx, leg_num in enumerate(distinct_legs, start=2):
        ws_pivot.cell(row=1, column=col_idx, value=f"Leg {leg_num}").font = bold_font
    pivot_sum_col = len(distinct_legs) + 2
    ws_pivot.cell(row=1, column=pivot_sum_col, value="Sum").font = bold_font

    # Data rows: one per seat, with row sum
    pivot_col_sums = {leg: 0 for leg in distinct_legs}
    for row_idx, seat_num in enumerate(distinct_seats, start=2):
        ws_pivot.cell(row=row_idx, column=1, value=f"Seat {seat_num}")
        row_sum = 0
        for col_idx, leg_num in enumerate(distinct_legs, start=2):
            draw_val = seat_draw.get(leg_num, {}).get(seat_num, "")
            cell = ws_pivot.cell(row=row_idx, column=col_idx, value=draw_val)
            if ghost_map.get((leg_num, seat_num), False):
                cell.fill = ghost_fill
            # Add to sums (only if numeric)
            if isinstance(draw_val, (int, float)):
                row_sum += draw_val
                pivot_col_sums[leg_num] += draw_val
        # Row sum cell
        sum_cell = ws_pivot.cell(row=row_idx, column=pivot_sum_col, value=row_sum)
        sum_cell.font = bold_font

    # Column sums row at the bottom
    pivot_sum_row = len(distinct_seats) + 2
    ws_pivot.cell(row=pivot_sum_row, column=1, value="Sum").font = bold_font
    for col_idx, leg_num in enumerate(distinct_legs, start=2):
        sum_cell = ws_pivot.cell(row=pivot_sum_row, column=col_idx, value=pivot_col_sums[leg_num])
        sum_cell.font = bold_font

    # Save to BytesIO and return
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    event_name_safe = sanitize_filename(event.name) if event.name else f"Event_{event_id}"
    start_date_str = event.start_date.strftime("%d%m%Y") if event.start_date else "nodate"
    filename = f"{event_name_safe}_{start_date_str}_TA_Lineup.xlsx"

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/duration-estimate", response_model=TADurationEstimateResponse)
async def estimate_duration(
    data: TADurationEstimateRequest,
) -> dict:
    """
    Calculate estimated duration for a TA event.

    This is a utility endpoint that doesn't require authentication.
    Useful for planning events before creating them.
    """
    algorithm = map_pairing_algorithm(data.algorithm)

    result = TAPairingService.calculate_event_duration(
        num_participants=data.num_participants,
        algorithm=algorithm,
        match_duration_minutes=data.match_duration_minutes,
        break_between_rounds_minutes=data.break_between_rounds_minutes,
        custom_rounds=data.custom_rounds,
    )

    return result
