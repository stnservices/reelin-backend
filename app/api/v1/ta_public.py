"""Public Trout Area (TA) competition endpoints - No authentication required.

This module provides read-only public endpoints for TA events, enabling
live leaderboard viewing without login. Used by the /live/[id] web page.

Security:
- All endpoints are READ-ONLY (no write operations)
- Only published/ongoing/completed events are accessible
- Sensitive user data (email, phone) is NOT exposed
- Rate limiting should be applied at the infrastructure level
"""

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.event import Event, EventStatus
from app.models.user import UserAccount, UserProfile
from app.models.trout_area import (
    TAEventSettings,
    TAMatch,
    TAGameCard,
    TAQualifierStanding,
    TAKnockoutBracket,
    TATournamentPhase,
    TAMatchStatus,
    TAGameCardStatus,
)
from app.services.redis_cache import redis_cache

router = APIRouter(prefix="/ta/public", tags=["TA Public"])


# =============================================================================
# Response Models
# =============================================================================

class PublicEventStatusResponse(BaseModel):
    """Public event status for live page."""
    event_id: int
    event_type: str
    name: str
    status: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    current_phase: str
    has_knockout_bracket: bool
    total_legs: int
    completed_legs: int
    is_live: bool


class PublicStandingEntry(BaseModel):
    """Single standing entry for public display."""
    rank: Optional[int] = None  # None for users with no validated cards
    user_id: int
    display_name: str
    avatar_url: Optional[str] = None
    points: float
    total_catches: int
    victories: int
    ties: int
    losses: int
    position_change: int = 0  # Positive = moved up


class PublicStandingsResponse(BaseModel):
    """Public standings response."""
    event_id: int
    phase: str
    standings: list[PublicStandingEntry]
    last_updated: str


class PublicScheduleResponse(BaseModel):
    """Public schedule/progress info."""
    event_id: int
    current_phase: str
    current_leg: int
    total_legs: int
    legs_completed: int
    progress_percent: float
    phase_progress: dict  # Per-phase completion info


class PublicBracketParticipant(BaseModel):
    """Participant in bracket match."""
    user_id: Optional[int] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    position: Optional[int] = None  # Qualifier position or seed
    catches: Optional[int] = None
    is_winner: bool = False


class PublicBracketMatch(BaseModel):
    """Single match in bracket."""
    match_id: int
    phase: str
    leg_number: int
    participant_a: Optional[PublicBracketParticipant] = None
    participant_b: Optional[PublicBracketParticipant] = None
    status: str  # scheduled, ongoing, completed
    winner_id: Optional[int] = None


class PublicBracketResponse(BaseModel):
    """Full bracket for visualization."""
    event_id: int
    event_name: str
    current_phase: str
    qualifier_top_6: list[dict]  # Top 6 from qualifier for bracket preview
    requalification_matches: list[PublicBracketMatch]
    semifinal_matches: list[PublicBracketMatch]
    grand_final: Optional[PublicBracketMatch] = None
    small_final: Optional[PublicBracketMatch] = None


# =============================================================================
# Helper Functions
# =============================================================================

async def verify_public_event(db: AsyncSession, event_id: int) -> Event:
    """
    Verify event exists and is publicly viewable.
    Only published, ongoing, or completed events are accessible.
    """
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    allowed_statuses = [
        EventStatus.PUBLISHED.value,
        EventStatus.ONGOING.value,
        EventStatus.COMPLETED.value,
    ]
    if event.status not in allowed_statuses:
        raise HTTPException(status_code=404, detail="Event not found")

    return event


async def get_ta_settings(db: AsyncSession, event_id: int) -> Optional[TAEventSettings]:
    """Get TA settings for event."""
    query = select(TAEventSettings).where(TAEventSettings.event_id == event_id)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_previous_standings(event_id: int) -> dict[int, int]:
    """Get previous standings from Redis cache for position change calculation."""
    cache_key = f"ta_standings:{event_id}:previous"
    try:
        cached = await redis_cache.get(cache_key)
        if cached:
            data = json.loads(cached)
            return {s['user_id']: s['rank'] for s in data}
    except Exception:
        pass
    return {}


async def save_current_standings(event_id: int, standings: list[dict]):
    """Save current standings to Redis for next position change calculation."""
    cache_key = f"ta_standings:{event_id}:previous"
    try:
        await redis_cache.set(
            cache_key,
            json.dumps([{"user_id": s['user_id'], "rank": s['rank']} for s in standings]),
            ttl=86400  # 24 hours
        )
    except Exception:
        pass  # Non-critical, just skip caching


# =============================================================================
# Public Endpoints
# =============================================================================

@router.get("/events/{event_id}/status", response_model=PublicEventStatusResponse)
async def get_public_event_status(
    event_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    PUBLIC - Get event status for live page.

    Returns basic event info including:
    - Event type (for frontend routing)
    - Current phase
    - Knockout bracket availability
    - Leg progress
    """
    event = await verify_public_event(db, event_id)
    ta_settings = await get_ta_settings(db, event_id)

    current_phase = "qualifier"
    has_knockout = False
    total_legs = 0

    if ta_settings:
        has_knockout = ta_settings.has_knockout_stage or False
        total_legs = ta_settings.number_of_legs or 0

    # Determine actual current phase from knockout matches
    if has_knockout:
        # Check if knockout matches exist and their status
        knockout_query = select(TAMatch).where(
            TAMatch.event_id == event_id,
            TAMatch.phase.in_([
                TATournamentPhase.REQUALIFICATION.value,
                TATournamentPhase.SEMIFINAL.value,
                TATournamentPhase.FINAL_GRAND.value,
                TATournamentPhase.FINAL_SMALL.value,
            ])
        )
        knockout_result = await db.execute(knockout_query)
        knockout_matches = knockout_result.scalars().all()

        if knockout_matches:
            # Group by phase
            by_phase = {}
            for m in knockout_matches:
                if m.phase not in by_phase:
                    by_phase[m.phase] = []
                by_phase[m.phase].append(m)

            # Check completion status
            def all_completed(matches):
                return len(matches) > 0 and all(m.status == TAMatchStatus.COMPLETED.value for m in matches)

            finals = by_phase.get(TATournamentPhase.FINAL_GRAND.value, []) + by_phase.get(TATournamentPhase.FINAL_SMALL.value, [])
            semifinals = by_phase.get(TATournamentPhase.SEMIFINAL.value, [])
            requalifications = by_phase.get(TATournamentPhase.REQUALIFICATION.value, [])

            if finals and all_completed(finals):
                current_phase = "completed"
            elif finals:
                current_phase = "finals"
            elif semifinals and all_completed(semifinals):
                current_phase = "finals"
            elif semifinals:
                current_phase = "semifinal"
            elif requalifications and all_completed(requalifications):
                current_phase = "semifinal"
            elif requalifications:
                current_phase = "requalification"

    # Inline completed legs count (single query)
    completed_legs_result = await db.execute(
        select(func.count(func.distinct(TAGameCard.leg_number))).where(
            TAGameCard.event_id == event_id,
            TAGameCard.status == TAGameCardStatus.VALIDATED.value,
        )
    )
    completed_legs = completed_legs_result.scalar() or 0

    # Get event type from event_type relationship
    event_type_code = "trout_area"  # Default
    if event.event_type:
        event_type_code = event.event_type.code

    return PublicEventStatusResponse(
        event_id=event.id,
        event_type=event_type_code,
        name=event.name,
        status=event.status,
        start_date=event.start_date.isoformat() if event.start_date else None,
        end_date=event.end_date.isoformat() if event.end_date else None,
        current_phase=current_phase,
        has_knockout_bracket=has_knockout,
        total_legs=total_legs,
        completed_legs=completed_legs,
        is_live=event.status == EventStatus.ONGOING.value,
    )


@router.get("/events/{event_id}/standings", response_model=PublicStandingsResponse)
async def get_public_standings(
    event_id: int,
    phase: Optional[str] = Query(None, description="Filter by phase: qualifier, requalification, semifinal, final"),
    db: AsyncSession = Depends(get_db),
):
    """
    PUBLIC - Get TA standings for public display.

    Returns sanitized standings with:
    - Display name (no email/phone)
    - Avatar URL
    - Points, catches, W-T-L record
    - Position change since last leg

    Position changes are calculated by comparing with cached previous standings.
    """
    await verify_public_event(db, event_id)

    # Get standings from database (TAQualifierStanding is only for qualifier phase)
    query = select(TAQualifierStanding).where(
        TAQualifierStanding.event_id == event_id
    ).order_by(TAQualifierStanding.rank)

    result = await db.execute(query)
    standings_rows = result.scalars().all()

    # Get previous positions for change calculation
    previous_positions = await get_previous_standings(event_id)

    # Build response with user display names
    standings_list = []
    for standing in standings_rows:
        # Get user profile for display name and avatar
        profile_query = select(UserProfile).where(UserProfile.user_id == standing.user_id)
        profile_result = await db.execute(profile_query)
        profile = profile_result.scalar_one_or_none()

        display_name = f"User {standing.user_id}"  # Fallback
        avatar_url = None

        if profile:
            display_name = profile.full_name or f"User {standing.user_id}"
            avatar_url = profile.profile_picture_url

        # Calculate position change
        prev_rank = previous_positions.get(standing.user_id)
        position_change = 0
        if prev_rank is not None:
            position_change = prev_rank - standing.rank  # Positive = moved up

        standings_list.append({
            "rank": standing.rank,
            "user_id": standing.user_id,
            "display_name": display_name,
            "avatar_url": avatar_url,
            "points": float(standing.total_points),
            "total_catches": standing.total_fish_caught,
            "victories": standing.total_victories,
            "ties": (standing.ties_with_fish or 0) + (standing.ties_without_fish or 0),
            "losses": (standing.losses_with_fish or 0) + (standing.losses_without_fish or 0),
            "position_change": position_change,
        })

    # Save current standings for next comparison
    await save_current_standings(event_id, standings_list)

    return PublicStandingsResponse(
        event_id=event_id,
        phase=phase or "all",
        standings=[PublicStandingEntry(**s) for s in standings_list],
        last_updated=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/events/{event_id}/schedule", response_model=PublicScheduleResponse)
async def get_public_schedule(
    event_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    PUBLIC - Get current schedule/leg progress.

    Returns:
    - Current phase
    - Current leg number
    - Progress percentage
    - Per-phase breakdown
    """
    await verify_public_event(db, event_id)
    ta_settings = await get_ta_settings(db, event_id)

    current_phase = "qualifier"
    total_legs = 0

    if ta_settings:
        total_legs = ta_settings.number_of_legs or 0

    # Single query: completed legs, total legs (max leg_number), current leg
    stats_query = select(
        func.count(func.distinct(TAGameCard.leg_number)).filter(
            TAGameCard.status == TAGameCardStatus.VALIDATED.value
        ).label("completed_legs"),
        func.max(TAGameCard.leg_number).label("max_leg"),
    ).where(TAGameCard.event_id == event_id)
    stats_result = await db.execute(stats_query)
    stats_row = stats_result.one()

    total_completed = stats_row.completed_legs or 0
    total_all = stats_row.max_leg or 0
    current_leg = stats_row.max_leg or 1

    progress_percent = 0
    if total_all > 0:
        progress_percent = round((total_completed / total_all) * 100, 1)

    # TAGameCard has no phase column, so per-phase breakdown is not meaningful
    phase_progress = {}

    return PublicScheduleResponse(
        event_id=event_id,
        current_phase=current_phase,
        current_leg=current_leg,
        total_legs=total_legs,
        legs_completed=total_completed,
        progress_percent=progress_percent,
        phase_progress=phase_progress,
    )


@router.get("/events/{event_id}/bracket", response_model=PublicBracketResponse)
async def get_public_bracket(
    event_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    PUBLIC - Get knockout bracket for visualization.

    Only available for events with has_knockout_bracket = true.

    Returns:
    - Top 6 qualifier standings (bracket seeds)
    - Requalification matches
    - Semifinal matches
    - Grand/Small final matches
    """
    event = await verify_public_event(db, event_id)
    ta_settings = await get_ta_settings(db, event_id)

    if not ta_settings or not ta_settings.has_knockout_stage:
        raise HTTPException(
            status_code=404,
            detail="No knockout bracket for this event"
        )

    # Get top 6 from qualifier standings
    qualifier_query = select(TAQualifierStanding).where(
        TAQualifierStanding.event_id == event_id,
        TAQualifierStanding.rank <= 6,
    ).order_by(TAQualifierStanding.rank)

    result = await db.execute(qualifier_query)
    qualifier_standings = result.scalars().all()

    # Build top 6 with display names
    qualifier_top_6 = []
    for standing in qualifier_standings:
        profile_query = select(UserProfile).where(UserProfile.user_id == standing.user_id)
        profile_result = await db.execute(profile_query)
        profile = profile_result.scalar_one_or_none()

        display_name = profile.full_name if profile else f"User {standing.user_id}"

        advances_to = "eliminated"
        if standing.rank <= 2:
            advances_to = f"semifinal_{['a', 'b'][standing.rank - 1]}"
        elif standing.rank <= 6:
            advances_to = "requalification"

        qualifier_top_6.append({
            "position": standing.rank,
            "user_id": standing.user_id,
            "display_name": display_name,
            "points": float(standing.total_points),
            "advances_to": advances_to,
        })

    # Get knockout matches
    async def get_matches_for_phase(phase: str) -> list[PublicBracketMatch]:
        matches_query = select(TAMatch).where(
            TAMatch.event_id == event_id,
            TAMatch.phase == phase,
        ).order_by(TAMatch.leg_number, TAMatch.match_number)

        result = await db.execute(matches_query)
        matches = result.scalars().all()

        bracket_matches = []
        for match in matches:
            # Get participant info
            participant_a = None
            participant_b = None

            # Determine winner from outcome_code ("V" = Victory)
            a_is_winner = match.competitor_a_outcome_code == "V"
            b_is_winner = match.competitor_b_outcome_code == "V"
            winner_id = None
            if a_is_winner:
                winner_id = match.competitor_a_id
            elif b_is_winner:
                winner_id = match.competitor_b_id

            if match.competitor_a_id:
                profile_a = await db.execute(
                    select(UserProfile).where(UserProfile.user_id == match.competitor_a_id)
                )
                profile_a = profile_a.scalar_one_or_none()
                participant_a = PublicBracketParticipant(
                    user_id=match.competitor_a_id,
                    display_name=profile_a.full_name if profile_a else f"User {match.competitor_a_id}",
                    avatar_url=profile_a.profile_picture_url if profile_a else None,
                    catches=match.competitor_a_catches,
                    is_winner=a_is_winner,
                )

            if match.competitor_b_id:
                profile_b = await db.execute(
                    select(UserProfile).where(UserProfile.user_id == match.competitor_b_id)
                )
                profile_b = profile_b.scalar_one_or_none()
                participant_b = PublicBracketParticipant(
                    user_id=match.competitor_b_id,
                    display_name=profile_b.full_name if profile_b else f"User {match.competitor_b_id}",
                    avatar_url=profile_b.profile_picture_url if profile_b else None,
                    catches=match.competitor_b_catches,
                    is_winner=b_is_winner,
                )

            # Determine status
            status = "scheduled"
            if match.status == TAMatchStatus.COMPLETED.value:
                status = "completed"
            elif match.status == TAMatchStatus.IN_PROGRESS.value:
                status = "ongoing"

            bracket_matches.append(PublicBracketMatch(
                match_id=match.id,
                phase=match.phase,
                leg_number=match.leg_number,
                participant_a=participant_a,
                participant_b=participant_b,
                status=status,
                winner_id=winner_id,
            ))

        return bracket_matches

    requalification_matches = await get_matches_for_phase(TATournamentPhase.REQUALIFICATION.value)
    semifinal_matches = await get_matches_for_phase(TATournamentPhase.SEMIFINAL.value)
    grand_final_matches = await get_matches_for_phase(TATournamentPhase.FINAL_GRAND.value)
    small_final_matches = await get_matches_for_phase(TATournamentPhase.FINAL_SMALL.value)

    # Determine current phase based on match statuses
    def all_completed(matches: list) -> bool:
        return len(matches) > 0 and all(m.status == "completed" for m in matches)

    def any_exists(matches: list) -> bool:
        return len(matches) > 0

    current_phase = "qualifier"
    if any_exists(grand_final_matches) or any_exists(small_final_matches):
        if all_completed(grand_final_matches) and all_completed(small_final_matches):
            current_phase = "completed"
        else:
            current_phase = "finals"
    elif any_exists(semifinal_matches):
        if all_completed(semifinal_matches):
            current_phase = "finals"
        else:
            current_phase = "semifinal"
    elif any_exists(requalification_matches):
        if all_completed(requalification_matches):
            current_phase = "semifinal"
        else:
            current_phase = "requalification"

    return PublicBracketResponse(
        event_id=event_id,
        event_name=event.name,
        current_phase=current_phase,
        qualifier_top_6=qualifier_top_6,
        requalification_matches=requalification_matches,
        semifinal_matches=semifinal_matches,
        grand_final=grand_final_matches[0] if grand_final_matches else None,
        small_final=small_final_matches[0] if small_final_matches else None,
    )
