"""Admin statistics endpoints for manual recalculation and debugging."""

from typing import Optional, List, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, distinct, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import UserAccount
from app.models.event import Event
from app.models.enrollment import EventEnrollment
from app.models.trout_area import TALineup
from app.models.statistics import UserEventTypeStats
from app.core.permissions import AdminOnly
from app.services.statistics_service import statistics_service


router = APIRouter()


# ============== Response Schemas ==============

class AdminRankingEntry(BaseModel):
    """Single ranking entry with user details."""
    rank: int
    user_id: int
    user_name: str
    email: str
    avatar_url: Optional[str] = None
    total_score: int = 0
    position_points: int = 0
    podium_bonus: int = 0
    participation_weight: int = 0
    participations: int = 0
    total_leaderboard_points: float = 0.0
    avg_catches_per_event: float = 0.0
    best_single_catch: float = 0.0
    gold_count: int = 0
    silver_count: int = 0
    bronze_count: int = 0


class AdminRankingsResponse(BaseModel):
    """Response for full admin rankings."""
    rankings: List[AdminRankingEntry]
    format_code: str
    year: Optional[int]
    total: int
    available_years: List[int] = Field(default_factory=list)


class RankingEventBreakdown(BaseModel):
    """Single event contribution to ranking."""
    event_id: int
    event_name: str
    event_date: str
    rank: int
    points: float
    catches: int
    is_national: bool
    position_points: int = 0
    podium_bonus: int = 0


class UserRankingBreakdownResponse(BaseModel):
    """Response with events contributing to user's ranking."""
    user_id: int
    user_name: str
    format_code: str
    year: Optional[int]
    events: List[RankingEventBreakdown]
    total_score: int = 0


class StoredStats(BaseModel):
    """Stats from user_event_type_stats table."""
    total_points: float = 0
    total_catches: int = 0
    total_events: int = 0
    total_wins: int = 0
    podiums: int = 0
    largest_catch: Optional[float] = None


class CalculatedStats(BaseModel):
    """Stats calculated from event_scoreboards."""
    total_points: float = 0
    total_catches: int = 0
    total_events: int = 0
    total_wins: int = 0
    podiums: int = 0
    largest_catch: Optional[float] = None


class StatsComparison(BaseModel):
    """Comparison between stored and calculated stats."""
    format_code: str
    stored: StoredStats
    calculated: CalculatedStats
    discrepancies: List[str] = Field(default_factory=list)


class UserStatsComparisonResponse(BaseModel):
    """Response with stored vs calculated stats comparison."""
    user_id: int
    user_name: str
    email: str
    sf: Optional[StatsComparison] = None
    ta: Optional[StatsComparison] = None
    overall: Optional[StatsComparison] = None


class UserSearchResult(BaseModel):
    """User search result for admin."""
    id: int
    email: str
    display_name: str
    avatar_url: Optional[str] = None


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


# ============== New Admin Endpoints ==============


@router.get("/rankings", response_model=AdminRankingsResponse)
async def get_admin_rankings(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
    format_code: str = Query("sf", pattern="^(sf|ta)$", description="Format: sf or ta"),
    year: Optional[int] = Query(None, ge=2000, le=2100, description="Year filter"),
    limit: int = Query(100, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    search: Optional[str] = Query(None, description="Search by name or email"),
) -> AdminRankingsResponse:
    """
    Get full rankings with user details for admin dashboard.

    Returns all ranked users (not just top 10) with their email and full stats.
    Supports pagination and search by name/email.
    """
    # Get available years
    years_query = text("""
        SELECT DISTINCT EXTRACT(YEAR FROM start_date)::integer as year
        FROM events
        WHERE is_national_event = TRUE AND status = 'completed'
        ORDER BY year DESC
    """)
    years_result = await db.execute(years_query)
    available_years = [row.year for row in years_result.fetchall()]

    # Build the rankings query
    if year:
        # Specific year from top_anglers_ranking view
        base_query = """
            SELECT
                r.user_id,
                p.first_name || ' ' || p.last_name as user_name,
                u.email,
                p.profile_picture_url as avatar_url,
                r.participations,
                r.total_position_points as position_points,
                r.total_podium_bonus as podium_bonus,
                r.participation_weight,
                r.total_score,
                r.total_leaderboard_points,
                r.avg_catches_per_event,
                r.best_single_catch,
                r.gold_count,
                r.silver_count,
                r.bronze_count
            FROM top_anglers_ranking r
            JOIN user_profiles p ON p.user_id = r.user_id
            JOIN user_accounts u ON u.id = r.user_id
            WHERE r.format_code = :format_code AND r.competition_year = :year
        """
        params: dict = {"format_code": format_code, "year": year}
    else:
        # All-time from top_anglers_all_time view
        base_query = """
            SELECT
                r.user_id,
                p.first_name || ' ' || p.last_name as user_name,
                u.email,
                p.profile_picture_url as avatar_url,
                r.participations,
                r.total_position_points as position_points,
                r.total_podium_bonus as podium_bonus,
                r.participation_weight,
                r.total_score,
                r.total_leaderboard_points,
                r.avg_catches_per_event,
                r.best_single_catch,
                r.gold_count,
                r.silver_count,
                r.bronze_count
            FROM top_anglers_all_time r
            JOIN user_profiles p ON p.user_id = r.user_id
            JOIN user_accounts u ON u.id = r.user_id
            WHERE r.format_code = :format_code
        """
        params = {"format_code": format_code}

    # Add search filter
    if search:
        base_query += " AND (LOWER(p.first_name || ' ' || p.last_name) LIKE LOWER(:search) OR LOWER(u.email) LIKE LOWER(:search))"
        params["search"] = f"%{search}%"

    # Get total count
    count_query = text(f"SELECT COUNT(*) FROM ({base_query}) subq")
    count_result = await db.execute(count_query, params)
    total = count_result.scalar() or 0

    # Add ordering and pagination
    full_query = text(f"""
        {base_query}
        ORDER BY
            total_score DESC,
            total_leaderboard_points DESC,
            avg_catches_per_event DESC,
            best_single_catch DESC
        LIMIT :limit OFFSET :offset
    """)
    params["limit"] = limit
    params["offset"] = offset

    result = await db.execute(full_query, params)
    rows = result.fetchall()

    # Build response with rank calculation
    rankings: List[AdminRankingEntry] = []
    for idx, row in enumerate(rows, start=offset + 1):
        rankings.append(AdminRankingEntry(
            rank=idx,
            user_id=row.user_id,
            user_name=row.user_name or "Unknown",
            email=row.email or "",
            avatar_url=row.avatar_url,
            total_score=row.total_score or 0,
            position_points=row.position_points or 0,
            podium_bonus=row.podium_bonus or 0,
            participation_weight=row.participation_weight or 0,
            participations=row.participations or 0,
            total_leaderboard_points=row.total_leaderboard_points or 0.0,
            avg_catches_per_event=row.avg_catches_per_event or 0.0,
            best_single_catch=row.best_single_catch or 0.0,
            gold_count=row.gold_count or 0,
            silver_count=row.silver_count or 0,
            bronze_count=row.bronze_count or 0,
        ))

    return AdminRankingsResponse(
        rankings=rankings,
        format_code=format_code,
        year=year,
        total=total,
        available_years=available_years,
    )


@router.get("/users/{user_id}/ranking-breakdown", response_model=UserRankingBreakdownResponse)
async def get_user_ranking_breakdown(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
    format_code: str = Query("sf", pattern="^(sf|ta)$", description="Format: sf or ta"),
    year: Optional[int] = Query(None, ge=2000, le=2100, description="Year filter"),
) -> UserRankingBreakdownResponse:
    """
    Get breakdown of events contributing to a user's ranking.

    Shows each national event the user participated in with their rank,
    points, and how it contributed to their total score.
    """
    # Get user info
    user = await db.get(UserAccount, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get user profile
    profile_result = await db.execute(
        text("SELECT first_name, last_name FROM user_profiles WHERE user_id = :user_id"),
        {"user_id": user_id}
    )
    profile_row = profile_result.fetchone()
    user_name = f"{profile_row.first_name} {profile_row.last_name}" if profile_row else "Unknown"

    # Query events for this user
    # For SF, use event_scoreboards table
    if format_code == "sf":
        query = text("""
            SELECT
                e.id as event_id,
                e.name as event_name,
                e.start_date::text as event_date,
                es.rank,
                es.total_score as points,
                es.validated_catches as catches,
                e.is_national_event as is_national,
                CASE
                    WHEN es.rank = 1 THEN 100
                    WHEN es.rank = 2 THEN 85
                    WHEN es.rank = 3 THEN 70
                    WHEN es.rank = 4 THEN 60
                    WHEN es.rank = 5 THEN 50
                    WHEN es.rank = 6 THEN 42
                    WHEN es.rank = 7 THEN 36
                    WHEN es.rank = 8 THEN 30
                    WHEN es.rank = 9 THEN 25
                    WHEN es.rank = 10 THEN 20
                    WHEN es.rank BETWEEN 11 AND 20 THEN 21 - es.rank + 5
                    ELSE 5
                END as position_points,
                CASE
                    WHEN es.rank = 1 THEN 25
                    WHEN es.rank = 2 THEN 15
                    WHEN es.rank = 3 THEN 10
                    ELSE 0
                END as podium_bonus
            FROM event_scoreboards es
            JOIN events e ON e.id = es.event_id
            JOIN event_types et ON et.id = e.event_type_id
            WHERE es.user_id = :user_id
              AND et.code = 'sf'
              AND e.status = 'completed'
        """)
    else:
        # For TA, use ta_qualifier_standings for points
        query = text("""
            SELECT
                e.id as event_id,
                e.name as event_name,
                e.start_date::text as event_date,
                qs.final_rank as rank,
                qs.total_wins as points,
                COALESCE(qs.total_catches, 0) as catches,
                e.is_national_event as is_national,
                CASE
                    WHEN qs.final_rank = 1 THEN 100
                    WHEN qs.final_rank = 2 THEN 85
                    WHEN qs.final_rank = 3 THEN 70
                    WHEN qs.final_rank = 4 THEN 60
                    WHEN qs.final_rank = 5 THEN 50
                    WHEN qs.final_rank = 6 THEN 42
                    WHEN qs.final_rank = 7 THEN 36
                    WHEN qs.final_rank = 8 THEN 30
                    WHEN qs.final_rank = 9 THEN 25
                    WHEN qs.final_rank = 10 THEN 20
                    WHEN qs.final_rank BETWEEN 11 AND 20 THEN 21 - qs.final_rank + 5
                    ELSE 5
                END as position_points,
                CASE
                    WHEN qs.final_rank = 1 THEN 25
                    WHEN qs.final_rank = 2 THEN 15
                    WHEN qs.final_rank = 3 THEN 10
                    ELSE 0
                END as podium_bonus
            FROM ta_qualifier_standings qs
            JOIN events e ON e.id = qs.event_id
            JOIN event_types et ON et.id = e.event_type_id
            WHERE qs.user_id = :user_id
              AND et.code = 'ta'
              AND e.status = 'completed'
        """)

    params: dict = {"user_id": user_id}

    # Add year filter if specified
    if year:
        query = text(str(query) + " AND EXTRACT(YEAR FROM e.start_date) = :year ORDER BY e.start_date DESC")
        params["year"] = year
    else:
        query = text(str(query) + " ORDER BY e.start_date DESC")

    result = await db.execute(query, params)
    rows = result.fetchall()

    events: List[RankingEventBreakdown] = []
    total_score = 0

    for row in rows:
        event = RankingEventBreakdown(
            event_id=row.event_id,
            event_name=row.event_name,
            event_date=row.event_date[:10] if row.event_date else "",
            rank=row.rank or 0,
            points=float(row.points or 0),
            catches=row.catches or 0,
            is_national=row.is_national or False,
            position_points=row.position_points or 0,
            podium_bonus=row.podium_bonus or 0,
        )
        events.append(event)

        # Calculate total score from national events only
        if row.is_national:
            total_score += (row.position_points or 0) + (row.podium_bonus or 0) + 3  # +3 participation

    return UserRankingBreakdownResponse(
        user_id=user_id,
        user_name=user_name,
        format_code=format_code,
        year=year,
        events=events,
        total_score=total_score,
    )


@router.get("/users/{user_id}/stats-comparison", response_model=UserStatsComparisonResponse)
async def get_user_stats_comparison(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> UserStatsComparisonResponse:
    """
    Compare stored vs calculated statistics for a user.

    This helps identify data inconsistencies between user_event_type_stats
    and the actual data in event_scoreboards/ta tables.
    """
    # Get user info
    user = await db.get(UserAccount, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get user profile
    profile_result = await db.execute(
        text("SELECT first_name, last_name FROM user_profiles WHERE user_id = :user_id"),
        {"user_id": user_id}
    )
    profile_row = profile_result.fetchone()
    user_name = f"{profile_row.first_name} {profile_row.last_name}" if profile_row else "Unknown"

    # Get stored stats from user_event_type_stats
    stored_stats_query = text("""
        SELECT
            et.code as format_code,
            uets.total_points,
            uets.total_catches,
            uets.total_events,
            uets.total_wins,
            uets.podiums,
            uets.largest_catch
        FROM user_event_type_stats uets
        LEFT JOIN event_types et ON et.id = uets.event_type_id
        WHERE uets.user_id = :user_id
    """)
    stored_result = await db.execute(stored_stats_query, {"user_id": user_id})
    stored_rows = {row.format_code: row for row in stored_result.fetchall()}

    # Calculate SF stats from event_scoreboards
    sf_calc_query = text("""
        SELECT
            COUNT(DISTINCT es.event_id) as total_events,
            COALESCE(SUM(es.total_score), 0) as total_points,
            COALESCE(SUM(es.validated_catches), 0) as total_catches,
            COUNT(CASE WHEN es.rank = 1 THEN 1 END) as total_wins,
            COUNT(CASE WHEN es.rank <= 3 THEN 1 END) as podiums,
            MAX(es.best_catch) as largest_catch
        FROM event_scoreboards es
        JOIN events e ON e.id = es.event_id
        JOIN event_types et ON et.id = e.event_type_id
        WHERE es.user_id = :user_id AND et.code = 'sf' AND e.status = 'completed'
    """)
    sf_calc_result = await db.execute(sf_calc_query, {"user_id": user_id})
    sf_calc_row = sf_calc_result.fetchone()

    # Calculate TA stats from ta_qualifier_standings
    ta_calc_query = text("""
        SELECT
            COUNT(DISTINCT qs.event_id) as total_events,
            COALESCE(SUM(qs.total_wins), 0) as total_points,
            COALESCE(SUM(qs.total_catches), 0) as total_catches,
            COUNT(CASE WHEN qs.final_rank = 1 THEN 1 END) as total_wins,
            COUNT(CASE WHEN qs.final_rank <= 3 THEN 1 END) as podiums
        FROM ta_qualifier_standings qs
        JOIN events e ON e.id = qs.event_id
        JOIN event_types et ON et.id = e.event_type_id
        WHERE qs.user_id = :user_id AND et.code = 'ta' AND e.status = 'completed'
    """)
    ta_calc_result = await db.execute(ta_calc_query, {"user_id": user_id})
    ta_calc_row = ta_calc_result.fetchone()

    def compare_stats(format_code: str, stored_row: Any, calc_row: Any) -> StatsComparison:
        """Compare stored vs calculated and find discrepancies."""
        stored = StoredStats(
            total_points=float(stored_row.total_points or 0) if stored_row else 0,
            total_catches=int(stored_row.total_catches or 0) if stored_row else 0,
            total_events=int(stored_row.total_events or 0) if stored_row else 0,
            total_wins=int(stored_row.total_wins or 0) if stored_row else 0,
            podiums=int(stored_row.podiums or 0) if stored_row else 0,
            largest_catch=float(stored_row.largest_catch) if stored_row and stored_row.largest_catch else None,
        )

        calculated = CalculatedStats(
            total_points=float(calc_row.total_points or 0) if calc_row else 0,
            total_catches=int(calc_row.total_catches or 0) if calc_row else 0,
            total_events=int(calc_row.total_events or 0) if calc_row else 0,
            total_wins=int(calc_row.total_wins or 0) if calc_row else 0,
            podiums=int(calc_row.podiums or 0) if calc_row else 0,
            largest_catch=float(calc_row.largest_catch) if calc_row and hasattr(calc_row, 'largest_catch') and calc_row.largest_catch else None,
        )

        discrepancies = []
        if stored.total_points != calculated.total_points:
            discrepancies.append(f"total_points: stored={stored.total_points}, calculated={calculated.total_points}")
        if stored.total_catches != calculated.total_catches:
            discrepancies.append(f"total_catches: stored={stored.total_catches}, calculated={calculated.total_catches}")
        if stored.total_events != calculated.total_events:
            discrepancies.append(f"total_events: stored={stored.total_events}, calculated={calculated.total_events}")
        if stored.total_wins != calculated.total_wins:
            discrepancies.append(f"total_wins: stored={stored.total_wins}, calculated={calculated.total_wins}")
        if stored.podiums != calculated.podiums:
            discrepancies.append(f"podiums: stored={stored.podiums}, calculated={calculated.podiums}")

        return StatsComparison(
            format_code=format_code,
            stored=stored,
            calculated=calculated,
            discrepancies=discrepancies,
        )

    # Build response
    sf_comparison = compare_stats("sf", stored_rows.get("sf"), sf_calc_row)
    ta_comparison = compare_stats("ta", stored_rows.get("ta"), ta_calc_row)

    # Overall comparison (sum of both)
    overall_stored = stored_rows.get(None)  # event_type_id=NULL is overall
    overall_comparison = None
    if overall_stored or sf_calc_row or ta_calc_row:
        overall_calc_total_points = (sf_calc_row.total_points or 0) + (ta_calc_row.total_points or 0) if sf_calc_row and ta_calc_row else 0
        overall_calc_total_catches = (sf_calc_row.total_catches or 0) + (ta_calc_row.total_catches or 0) if sf_calc_row and ta_calc_row else 0
        overall_calc_total_events = (sf_calc_row.total_events or 0) + (ta_calc_row.total_events or 0) if sf_calc_row and ta_calc_row else 0
        overall_calc_total_wins = (sf_calc_row.total_wins or 0) + (ta_calc_row.total_wins or 0) if sf_calc_row and ta_calc_row else 0
        overall_calc_podiums = (sf_calc_row.podiums or 0) + (ta_calc_row.podiums or 0) if sf_calc_row and ta_calc_row else 0

        class OverallCalcRow:
            def __init__(self):
                self.total_points = overall_calc_total_points
                self.total_catches = overall_calc_total_catches
                self.total_events = overall_calc_total_events
                self.total_wins = overall_calc_total_wins
                self.podiums = overall_calc_podiums

        overall_comparison = compare_stats("overall", overall_stored, OverallCalcRow())

    return UserStatsComparisonResponse(
        user_id=user_id,
        user_name=user_name,
        email=user.email,
        sf=sf_comparison if sf_comparison.stored.total_events > 0 or sf_comparison.calculated.total_events > 0 else None,
        ta=ta_comparison if ta_comparison.stored.total_events > 0 or ta_comparison.calculated.total_events > 0 else None,
        overall=overall_comparison,
    )
