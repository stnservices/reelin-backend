"""Pro-exclusive API endpoints for ReelIn Pro users.

These endpoints provide premium features like catch maps, advanced stats, and data export.
"""

import logging
from datetime import datetime, timezone
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, or_, func, extract
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models import UserAccount, ProSubscription, ProGrant, SubscriptionStatus
from app.models.catch import Catch, CatchStatus, EventScoreboard
from app.models.event import Event, EventStatus
from app.schemas.catch import CatchMapItem, CatchMapResponse, ClusterHint

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pro", tags=["pro"])


# ============== Helper Functions ==============


async def is_user_pro(user_id: int, db: AsyncSession) -> bool:
    """Check if user has active Pro access (subscription or grant)."""
    # Check for active Stripe subscription
    subscription_query = select(ProSubscription).where(
        ProSubscription.user_id == user_id,
        ProSubscription.status.in_([
            SubscriptionStatus.ACTIVE.value,
            SubscriptionStatus.TRIALING.value,
        ]),
    )
    result = await db.execute(subscription_query)
    if result.scalar_one_or_none():
        return True

    # Check for active manual grant
    now = datetime.now(timezone.utc)
    grant_query = select(ProGrant).where(
        ProGrant.user_id == user_id,
        ProGrant.is_active == True,
        or_(
            ProGrant.expires_at.is_(None),  # Lifetime
            ProGrant.expires_at > now,
        ),
    )
    result = await db.execute(grant_query)
    if result.scalar_one_or_none():
        return True

    return False


def calculate_cluster_hints(catches: list, threshold: int = 50) -> list[ClusterHint] | None:
    """
    Calculate clustering hints for large datasets.
    Only returns hints if there are many catches in the same area.
    """
    if len(catches) < threshold:
        return None

    # Simple grid-based clustering
    # Divide world into ~100km grid cells
    grid_size = 1.0  # ~111km per degree
    clusters: dict[tuple, list] = {}

    for catch in catches:
        if catch.location_lat is None or catch.location_lng is None:
            continue
        cell = (
            int(catch.location_lat / grid_size),
            int(catch.location_lng / grid_size),
        )
        if cell not in clusters:
            clusters[cell] = []
        clusters[cell].append(catch)

    # Only return clusters with significant density
    hints = []
    for cell, cell_catches in clusters.items():
        if len(cell_catches) >= 5:
            lats = [c.location_lat for c in cell_catches]
            lngs = [c.location_lng for c in cell_catches]
            hints.append(ClusterHint(
                center_lat=sum(lats) / len(lats),
                center_lng=sum(lngs) / len(lngs),
                count=len(cell_catches),
                bounds={
                    "north": max(lats),
                    "south": min(lats),
                    "east": max(lngs),
                    "west": min(lngs),
                },
            ))

    return hints if hints else None


# ============== Endpoints ==============


@router.get("/events/{event_id}/catches/map", response_model=CatchMapResponse)
async def get_event_catch_map(
    event_id: int,
    species_id: int | None = Query(None, description="Filter by species ID"),
    user_id: int | None = Query(None, description="Filter by user ID"),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get catch locations for map display.

    - **Pro users**: See all validated catches with locations
    - **Free users**: See only their own catches

    Requires event to be completed. Returns catches with location data only.
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    result = await db.execute(event_query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    # Check event is completed
    if event.status != EventStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Catch map is only available for completed events",
        )

    # Check if user is Pro
    is_pro = await is_user_pro(current_user.id, db)

    # Build query for catches with location data
    query = (
        select(Catch)
        .options(
            selectinload(Catch.user).selectinload(UserAccount.profile),
            selectinload(Catch.fish),
        )
        .where(
            Catch.event_id == event_id,
            Catch.status == CatchStatus.APPROVED.value,
            Catch.location_lat.isnot(None),
            Catch.location_lng.isnot(None),
        )
    )

    # Free users only see their own catches
    showing_own_only = False
    if not is_pro:
        query = query.where(Catch.user_id == current_user.id)
        showing_own_only = True

    # Apply filters
    if species_id:
        query = query.where(Catch.fish_id == species_id)

    if user_id:
        # Pro users can filter by any user, free users are already filtered
        if is_pro:
            query = query.where(Catch.user_id == user_id)

    # Execute query
    result = await db.execute(query)
    catches = result.scalars().all()

    # Build map items
    map_items = [
        CatchMapItem.from_catch(catch)
        for catch in catches
    ]

    # Calculate cluster hints for large datasets
    cluster_hints = calculate_cluster_hints(catches)

    return CatchMapResponse(
        event_id=event_id,
        catches=map_items,
        total_catches=len(map_items),
        species_filter=species_id,
        user_filter=user_id if is_pro else None,
        is_pro_user=is_pro,
        showing_own_catches_only=showing_own_only,
        cluster_hints=cluster_hints,
    )


# ============== Advanced Statistics ==============


class PersonalBestItem(BaseModel):
    """Personal best catch for a species."""

    species_id: int
    species_name: str
    length_cm: float
    weight_kg: float | None
    event_id: int
    event_name: str
    caught_at: datetime


class SpeciesBreakdown(BaseModel):
    """Species breakdown for pie chart."""

    species_id: int
    species_name: str
    count: int
    percentage: float


class TimeAnalysis(BaseModel):
    """Time-based analysis."""

    best_hour: int | None  # 0-23
    best_day_of_week: int | None  # 0=Monday, 6=Sunday
    catches_by_hour: dict[int, int]  # hour -> count
    catches_by_day: dict[int, int]  # day -> count


class EventPerformance(BaseModel):
    """Event performance statistics."""

    total_events: int
    wins: int
    podium_finishes: int  # Top 3
    top_10_finishes: int
    average_placement: float | None
    best_placement: int | None
    total_catches: int
    total_points: float


class ImprovementTrend(BaseModel):
    """Improvement trend over time."""

    period: str  # e.g., "2024-01", "2024-02"
    average_length: float
    catch_count: int


class AdvancedStatsResponse(BaseModel):
    """Advanced statistics response."""

    is_pro_user: bool
    personal_bests: list[PersonalBestItem]
    species_breakdown: list[SpeciesBreakdown]
    time_analysis: TimeAnalysis | None
    event_performance: EventPerformance
    improvement_trends: list[ImprovementTrend]
    total_catches: int
    total_species: int
    member_since: datetime | None


@router.get("/stats", response_model=AdvancedStatsResponse)
async def get_advanced_stats(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get advanced statistics for the current user.

    - **Pro users**: Full statistics
    - **Free users**: Basic stats only (personal bests, totals)
    """
    is_pro = await is_user_pro(current_user.id, db)

    # Get all approved catches for this user
    catches_query = (
        select(Catch)
        .options(
            selectinload(Catch.fish),
            selectinload(Catch.event),
        )
        .where(
            Catch.user_id == current_user.id,
            Catch.status == CatchStatus.APPROVED.value,
        )
        .order_by(Catch.submitted_at.desc())
    )
    result = await db.execute(catches_query)
    catches = result.scalars().all()

    # Personal bests by species
    personal_bests = []
    species_best: dict[int, Catch] = {}
    for catch in catches:
        if catch.fish_id not in species_best or catch.length > species_best[catch.fish_id].length:
            species_best[catch.fish_id] = catch

    for catch in species_best.values():
        personal_bests.append(PersonalBestItem(
            species_id=catch.fish_id,
            species_name=catch.fish.name if catch.fish else "Unknown",
            length_cm=catch.length,
            weight_kg=catch.weight,
            event_id=catch.event_id,
            event_name=catch.event.name if catch.event else "Unknown",
            caught_at=catch.catch_time or catch.submitted_at,
        ))

    # Sort by length descending
    personal_bests.sort(key=lambda x: x.length_cm, reverse=True)

    # Species breakdown
    species_counts: dict[int, tuple[str, int]] = {}  # id -> (name, count)
    for catch in catches:
        species_id = catch.fish_id
        species_name = catch.fish.name if catch.fish else "Unknown"
        if species_id in species_counts:
            species_counts[species_id] = (species_name, species_counts[species_id][1] + 1)
        else:
            species_counts[species_id] = (species_name, 1)

    total_catches = len(catches)
    species_breakdown = [
        SpeciesBreakdown(
            species_id=sid,
            species_name=name,
            count=count,
            percentage=round((count / total_catches * 100) if total_catches > 0 else 0, 1),
        )
        for sid, (name, count) in species_counts.items()
    ]
    species_breakdown.sort(key=lambda x: x.count, reverse=True)

    # Time analysis (Pro only)
    time_analysis = None
    if is_pro and catches:
        catches_by_hour: dict[int, int] = defaultdict(int)
        catches_by_day: dict[int, int] = defaultdict(int)

        for catch in catches:
            catch_time = catch.catch_time or catch.submitted_at
            if catch_time:
                catches_by_hour[catch_time.hour] += 1
                catches_by_day[catch_time.weekday()] += 1

        best_hour = max(catches_by_hour.items(), key=lambda x: x[1])[0] if catches_by_hour else None
        best_day = max(catches_by_day.items(), key=lambda x: x[1])[0] if catches_by_day else None

        time_analysis = TimeAnalysis(
            best_hour=best_hour,
            best_day_of_week=best_day,
            catches_by_hour=dict(catches_by_hour),
            catches_by_day=dict(catches_by_day),
        )

    # Event performance
    scoreboard_query = (
        select(EventScoreboard)
        .options(selectinload(EventScoreboard.event))
        .where(EventScoreboard.user_id == current_user.id)
    )
    result = await db.execute(scoreboard_query)
    scoreboards = result.scalars().all()

    wins = sum(1 for s in scoreboards if s.rank == 1)
    podiums = sum(1 for s in scoreboards if s.rank and 1 <= s.rank <= 3)
    top_10s = sum(1 for s in scoreboards if s.rank and 1 <= s.rank <= 10)
    placements = [s.rank for s in scoreboards if s.rank and s.rank > 0]
    avg_placement = sum(placements) / len(placements) if placements else None
    best_placement = min(placements) if placements else None
    total_event_catches = sum(s.total_catches for s in scoreboards)
    total_points = sum(s.total_points for s in scoreboards)

    event_performance = EventPerformance(
        total_events=len(scoreboards),
        wins=wins,
        podium_finishes=podiums,
        top_10_finishes=top_10s,
        average_placement=round(avg_placement, 1) if avg_placement else None,
        best_placement=best_placement,
        total_catches=total_event_catches,
        total_points=total_points,
    )

    # Improvement trends (Pro only - by month)
    improvement_trends = []
    if is_pro and catches:
        monthly_data: dict[str, list[float]] = defaultdict(list)
        for catch in catches:
            catch_time = catch.catch_time or catch.submitted_at
            if catch_time:
                period = catch_time.strftime("%Y-%m")
                monthly_data[period].append(catch.length)

        for period in sorted(monthly_data.keys())[-12:]:  # Last 12 months
            lengths = monthly_data[period]
            improvement_trends.append(ImprovementTrend(
                period=period,
                average_length=round(sum(lengths) / len(lengths), 1),
                catch_count=len(lengths),
            ))

    # Member since
    member_since = current_user.created_at if current_user else None

    return AdvancedStatsResponse(
        is_pro_user=is_pro,
        personal_bests=personal_bests[:10],  # Top 10
        species_breakdown=species_breakdown[:10],  # Top 10
        time_analysis=time_analysis,
        event_performance=event_performance,
        improvement_trends=improvement_trends,
        total_catches=total_catches,
        total_species=len(species_counts),
        member_since=member_since,
    )


# ============== Data Export ==============


class ExportCatchItem(BaseModel):
    """Single catch item for export."""

    catch_id: int
    event_name: str
    species_name: str
    length_cm: float
    weight_kg: float | None
    points: float | None
    caught_at: datetime
    location_lat: float | None
    location_lng: float | None
    status: str


class ExportDataResponse(BaseModel):
    """Data export response with all catch data."""

    is_pro_user: bool
    catches: list[ExportCatchItem]
    total_catches: int
    export_date: datetime
    date_range_start: datetime | None
    date_range_end: datetime | None


@router.get("/export", response_model=ExportDataResponse)
async def export_catch_data(
    start_date: datetime | None = Query(None, description="Start date for export range"),
    end_date: datetime | None = Query(None, description="End date for export range"),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Export catch data for the current user.

    - **Pro users**: Full export with all data
    - **Free users**: Returns is_pro_user=False, use ProGate to block

    Optional date range filtering.
    """
    is_pro = await is_user_pro(current_user.id, db)

    # If not Pro, return empty with flag (mobile will show upgrade prompt)
    if not is_pro:
        return ExportDataResponse(
            is_pro_user=False,
            catches=[],
            total_catches=0,
            export_date=datetime.now(timezone.utc),
            date_range_start=start_date,
            date_range_end=end_date,
        )

    # Build query for catches
    query = (
        select(Catch)
        .options(
            selectinload(Catch.fish),
            selectinload(Catch.event),
        )
        .where(
            Catch.user_id == current_user.id,
            Catch.status == CatchStatus.APPROVED.value,
        )
        .order_by(Catch.submitted_at.desc())
    )

    # Apply date filters
    if start_date:
        query = query.where(Catch.submitted_at >= start_date)
    if end_date:
        query = query.where(Catch.submitted_at <= end_date)

    result = await db.execute(query)
    catches = result.scalars().all()

    # Build export items
    export_items = [
        ExportCatchItem(
            catch_id=catch.id,
            event_name=catch.event.name if catch.event else "Unknown",
            species_name=catch.fish.name if catch.fish else "Unknown",
            length_cm=catch.length,
            weight_kg=catch.weight,
            points=catch.points,
            caught_at=catch.catch_time or catch.submitted_at,
            location_lat=catch.location_lat,
            location_lng=catch.location_lng,
            status=catch.status,
        )
        for catch in catches
    ]

    return ExportDataResponse(
        is_pro_user=True,
        catches=export_items,
        total_catches=len(export_items),
        export_date=datetime.now(timezone.utc),
        date_range_start=start_date,
        date_range_end=end_date,
    )
