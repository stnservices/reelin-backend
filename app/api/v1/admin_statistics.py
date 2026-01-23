"""Admin statistics endpoints for manual recalculation and debugging."""

from datetime import datetime
from typing import Optional, List, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, distinct, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import UserAccount
from app.models.event import Event
from app.models.catch import Catch, CatchStatus
from app.models.enrollment import EventEnrollment
from app.models.fish import Fish
from app.models.trout_area import TALineup
from app.models.statistics import UserEventTypeStats
from app.models.achievement import UserAchievement, AchievementDefinition
from app.core.permissions import AdminOnly
from app.services.statistics_service import statistics_service
from app.services.achievement_service import AchievementService


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


# ============== Achievement Schemas ==============

class AchievementDetail(BaseModel):
    """Single achievement/badge detail."""
    achievement_id: int
    code: str
    name: str
    description: str
    category: str  # 'tiered' or 'special'
    tier: Optional[str] = None  # 'bronze', 'silver', 'gold', 'platinum'
    achievement_type: str
    threshold: Optional[int] = None
    icon_url: Optional[str] = None
    badge_color: Optional[str] = None
    earned_at: datetime
    event_id: Optional[int] = None
    event_name: Optional[str] = None
    fish_species: Optional[str] = None


class UserAchievementSummary(BaseModel):
    """Summary of achievements for a user."""
    user_id: int
    user_name: str
    email: str
    avatar_url: Optional[str] = None
    total_achievements: int = 0
    tiered_count: int = 0
    special_count: int = 0
    bronze_count: int = 0
    silver_count: int = 0
    gold_count: int = 0
    platinum_count: int = 0


class UserAchievementsResponse(BaseModel):
    """Response with user achievements details."""
    user_id: int
    user_name: str
    email: str
    summary: UserAchievementSummary
    achievements: List[AchievementDetail] = Field(default_factory=list)


class AchievementsListResponse(BaseModel):
    """Response for listing all users with achievements."""
    users: List[UserAchievementSummary]
    total: int


class AchievementRecalculateResponse(BaseModel):
    """Response for achievement recalculation."""
    success: bool
    message: str
    user_id: int
    achievements_before: int
    achievements_after: int
    new_achievements: List[str] = Field(default_factory=list)


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
    # Get available years (exclude test events)
    years_query = text("""
        SELECT DISTINCT EXTRACT(YEAR FROM start_date)::integer as year
        FROM events
        WHERE is_national_event = TRUE AND status = 'completed' AND is_test = FALSE
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

    # Query events for this user (exclude test events)
    # For SF, use event_scoreboards table
    if format_code == "sf":
        query = text("""
            SELECT
                e.id as event_id,
                e.name as event_name,
                e.start_date::text as event_date,
                es.rank,
                es.total_points as points,
                es.total_catches as catches,
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
              AND et.code = 'street_fishing'
              AND e.status = 'completed'
              AND e.is_test = FALSE
        """)
    else:
        # For TA, use ta_qualifier_standings for points (exclude test events)
        query = text("""
            SELECT
                e.id as event_id,
                e.name as event_name,
                e.start_date::text as event_date,
                qs.rank as rank,
                qs.total_points as points,
                COALESCE(qs.total_fish_caught, 0) as catches,
                e.is_national_event as is_national,
                CASE
                    WHEN qs.rank = 1 THEN 100
                    WHEN qs.rank = 2 THEN 85
                    WHEN qs.rank = 3 THEN 70
                    WHEN qs.rank = 4 THEN 60
                    WHEN qs.rank = 5 THEN 50
                    WHEN qs.rank = 6 THEN 42
                    WHEN qs.rank = 7 THEN 36
                    WHEN qs.rank = 8 THEN 30
                    WHEN qs.rank = 9 THEN 25
                    WHEN qs.rank = 10 THEN 20
                    WHEN qs.rank BETWEEN 11 AND 20 THEN 21 - qs.rank + 5
                    ELSE 5
                END as position_points,
                CASE
                    WHEN qs.rank = 1 THEN 25
                    WHEN qs.rank = 2 THEN 15
                    WHEN qs.rank = 3 THEN 10
                    ELSE 0
                END as podium_bonus
            FROM ta_qualifier_standings qs
            JOIN events e ON e.id = qs.event_id
            JOIN event_types et ON et.id = e.event_type_id
            WHERE qs.user_id = :user_id
              AND et.code = 'trout_area'
              AND e.status = 'completed'
              AND e.is_test = FALSE
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
            uets.podium_finishes as podiums,
            uets.largest_catch_cm as largest_catch
        FROM user_event_type_stats uets
        LEFT JOIN event_types et ON et.id = uets.event_type_id
        WHERE uets.user_id = :user_id
    """)
    stored_result = await db.execute(stored_stats_query, {"user_id": user_id})
    stored_rows = {row.format_code: row for row in stored_result.fetchall()}

    # Calculate SF stats - matches statistics_service._recalculate_stats
    # Points, wins, podiums from event_scoreboards; catches from catches table
    # Exclude test events
    sf_calc_query = text("""
        WITH scoreboard_stats AS (
            SELECT
                COUNT(DISTINCT es.event_id) as total_events,
                COALESCE(SUM(es.total_points), 0) as total_points,
                COUNT(CASE WHEN es.rank = 1 THEN 1 END) as total_wins,
                COUNT(CASE WHEN es.rank <= 3 THEN 1 END) as podiums,
                MAX(es.best_catch_length) as largest_catch
            FROM event_scoreboards es
            JOIN events e ON e.id = es.event_id
            JOIN event_types et ON et.id = e.event_type_id
            WHERE es.user_id = :user_id AND et.code = 'street_fishing' AND e.status = 'completed' AND e.is_test = FALSE
        ),
        catch_stats AS (
            SELECT COUNT(c.id) as total_catches
            FROM catches c
            JOIN events e ON e.id = c.event_id
            JOIN event_types et ON et.id = e.event_type_id
            WHERE c.user_id = :user_id AND et.code = 'street_fishing' AND e.is_test = FALSE
        )
        SELECT s.*, c.total_catches FROM scoreboard_stats s, catch_stats c
    """)
    sf_calc_result = await db.execute(sf_calc_query, {"user_id": user_id})
    sf_calc_row = sf_calc_result.fetchone()

    # Calculate TA stats - same approach as SF (exclude test events)
    ta_calc_query = text("""
        WITH scoreboard_stats AS (
            SELECT
                COUNT(DISTINCT es.event_id) as total_events,
                COALESCE(SUM(es.total_points), 0) as total_points,
                COUNT(CASE WHEN es.rank = 1 THEN 1 END) as total_wins,
                COUNT(CASE WHEN es.rank <= 3 THEN 1 END) as podiums,
                MAX(es.best_catch_length) as largest_catch
            FROM event_scoreboards es
            JOIN events e ON e.id = es.event_id
            JOIN event_types et ON et.id = e.event_type_id
            WHERE es.user_id = :user_id AND et.code = 'trout_area' AND e.status = 'completed' AND e.is_test = FALSE
        ),
        catch_stats AS (
            SELECT COUNT(c.id) as total_catches
            FROM catches c
            JOIN events e ON e.id = c.event_id
            JOIN event_types et ON et.id = e.event_type_id
            WHERE c.user_id = :user_id AND et.code = 'trout_area' AND e.is_test = FALSE
        )
        SELECT s.*, c.total_catches FROM scoreboard_stats s, catch_stats c
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
    sf_comparison = compare_stats("sf", stored_rows.get("street_fishing"), sf_calc_row)
    ta_comparison = compare_stats("ta", stored_rows.get("trout_area"), ta_calc_row)

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


# ============== Achievement Endpoints ==============


@router.get("/achievements", response_model=AchievementsListResponse)
async def get_all_user_achievements(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    search: Optional[str] = Query(None, description="Search by name or email"),
    limit: int = Query(100, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
) -> AchievementsListResponse:
    """
    Get all users with their achievement summary.

    Supports filtering by user_id or searching by name/email.
    Returns summary counts of achievements per user.
    """
    # Build base query for user achievement summaries
    query = text("""
        SELECT
            u.id as user_id,
            p.first_name || ' ' || p.last_name as user_name,
            u.email,
            p.profile_picture_url as avatar_url,
            COUNT(ua.id) as total_achievements,
            COUNT(ua.id) FILTER (WHERE ad.category = 'tiered') as tiered_count,
            COUNT(ua.id) FILTER (WHERE ad.category = 'special') as special_count,
            COUNT(ua.id) FILTER (WHERE ad.tier = 'bronze') as bronze_count,
            COUNT(ua.id) FILTER (WHERE ad.tier = 'silver') as silver_count,
            COUNT(ua.id) FILTER (WHERE ad.tier = 'gold') as gold_count,
            COUNT(ua.id) FILTER (WHERE ad.tier = 'platinum') as platinum_count
        FROM user_accounts u
        JOIN user_profiles p ON p.user_id = u.id
        LEFT JOIN user_achievements ua ON ua.user_id = u.id
        LEFT JOIN achievement_definitions ad ON ad.id = ua.achievement_id AND ad.is_active = TRUE
        WHERE 1=1
    """)
    params: dict = {}

    # Add user_id filter
    if user_id:
        query = text(str(query) + " AND u.id = :user_id")
        params["user_id"] = user_id

    # Add search filter
    if search:
        query = text(str(query) + " AND (LOWER(p.first_name || ' ' || p.last_name) LIKE LOWER(:search) OR LOWER(u.email) LIKE LOWER(:search))")
        params["search"] = f"%{search}%"

    # Only return users with achievements (unless filtering by user_id)
    if not user_id:
        query = text(str(query) + " GROUP BY u.id, p.first_name, p.last_name, u.email, p.profile_picture_url HAVING COUNT(ua.id) > 0")
    else:
        query = text(str(query) + " GROUP BY u.id, p.first_name, p.last_name, u.email, p.profile_picture_url")

    # Get total count
    count_query = text(f"SELECT COUNT(*) FROM ({query}) subq")
    count_result = await db.execute(count_query, params)
    total = count_result.scalar() or 0

    # Add ordering and pagination
    full_query = text(str(query) + " ORDER BY total_achievements DESC, user_name LIMIT :limit OFFSET :offset")
    params["limit"] = limit
    params["offset"] = offset

    result = await db.execute(full_query, params)
    rows = result.fetchall()

    users = [
        UserAchievementSummary(
            user_id=row.user_id,
            user_name=row.user_name or "Unknown",
            email=row.email or "",
            avatar_url=row.avatar_url,
            total_achievements=row.total_achievements or 0,
            tiered_count=row.tiered_count or 0,
            special_count=row.special_count or 0,
            bronze_count=row.bronze_count or 0,
            silver_count=row.silver_count or 0,
            gold_count=row.gold_count or 0,
            platinum_count=row.platinum_count or 0,
        )
        for row in rows
    ]

    return AchievementsListResponse(users=users, total=total)


@router.get("/users/{user_id}/achievements", response_model=UserAchievementsResponse)
async def get_user_achievements(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
    category: Optional[str] = Query(None, pattern="^(tiered|special)$", description="Filter by category"),
) -> UserAchievementsResponse:
    """
    Get detailed achievements for a specific user.

    Returns all achievements with full details including when earned,
    which event triggered it, and related fish species.
    """
    # Get user info
    user = await db.get(UserAccount, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get user profile
    profile_result = await db.execute(
        text("SELECT first_name, last_name, profile_picture_url FROM user_profiles WHERE user_id = :user_id"),
        {"user_id": user_id}
    )
    profile_row = profile_result.fetchone()
    user_name = f"{profile_row.first_name} {profile_row.last_name}" if profile_row else "Unknown"
    avatar_url = profile_row.profile_picture_url if profile_row else None

    # Get achievements with details
    query = text("""
        SELECT
            ad.id as achievement_id,
            ad.code,
            ad.name,
            ad.description,
            ad.category,
            ad.tier,
            ad.achievement_type,
            ad.threshold,
            ad.icon_url,
            ad.badge_color,
            ua.earned_at,
            ua.event_id,
            e.name as event_name,
            f.name as fish_species
        FROM user_achievements ua
        JOIN achievement_definitions ad ON ad.id = ua.achievement_id
        LEFT JOIN events e ON e.id = ua.event_id
        LEFT JOIN fish f ON f.id = ad.fish_id
        WHERE ua.user_id = :user_id AND ad.is_active = TRUE
    """)
    params: dict = {"user_id": user_id}

    if category:
        query = text(str(query) + " AND ad.category = :category")
        params["category"] = category

    query = text(str(query) + " ORDER BY ua.earned_at DESC, ad.sort_order")

    result = await db.execute(query, params)
    rows = result.fetchall()

    achievements = [
        AchievementDetail(
            achievement_id=row.achievement_id,
            code=row.code,
            name=row.name,
            description=row.description,
            category=row.category,
            tier=row.tier,
            achievement_type=row.achievement_type,
            threshold=row.threshold,
            icon_url=row.icon_url,
            badge_color=row.badge_color,
            earned_at=row.earned_at,
            event_id=row.event_id,
            event_name=row.event_name,
            fish_species=row.fish_species,
        )
        for row in rows
    ]

    # Calculate summary
    summary = UserAchievementSummary(
        user_id=user_id,
        user_name=user_name,
        email=user.email,
        avatar_url=avatar_url,
        total_achievements=len(achievements),
        tiered_count=sum(1 for a in achievements if a.category == "tiered"),
        special_count=sum(1 for a in achievements if a.category == "special"),
        bronze_count=sum(1 for a in achievements if a.tier == "bronze"),
        silver_count=sum(1 for a in achievements if a.tier == "silver"),
        gold_count=sum(1 for a in achievements if a.tier == "gold"),
        platinum_count=sum(1 for a in achievements if a.tier == "platinum"),
    )

    return UserAchievementsResponse(
        user_id=user_id,
        user_name=user_name,
        email=user.email,
        summary=summary,
        achievements=achievements,
    )


@router.post("/users/{user_id}/recalculate-achievements", response_model=AchievementRecalculateResponse)
async def recalculate_user_achievements(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> AchievementRecalculateResponse:
    """
    Recalculate and award any missing achievements for a user.

    This will:
    1. Check all tiered achievements based on current stats
    2. Check special achievements for each completed event
    3. Award any achievements that were missed

    Useful after data migrations or when achievements weren't properly triggered.
    """
    # Verify user exists
    user = await db.get(UserAccount, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get current achievement count
    before_count_result = await db.execute(
        select(func.count(UserAchievement.id)).where(UserAchievement.user_id == user_id)
    )
    achievements_before = before_count_result.scalar() or 0

    # Get user's completed events (non-test)
    events_query = (
        select(Event.id, Event.event_type_id)
        .join(EventEnrollment, EventEnrollment.event_id == Event.id)
        .where(EventEnrollment.user_id == user_id)
        .where(EventEnrollment.status == "approved")
        .where(Event.status == "completed")
        .where(Event.is_test == False)
        .order_by(Event.end_date)
    )
    events_result = await db.execute(events_query)
    events = events_result.fetchall()

    new_achievements: List[str] = []

    # Get user's approved catches from non-test events with fish info
    from sqlalchemy.orm import selectinload
    catches_query = (
        select(Catch)
        .join(Event, Event.id == Catch.event_id)
        .options(selectinload(Catch.fish))
        .where(Catch.user_id == user_id)
        .where(Catch.status == CatchStatus.APPROVED.value)
        .where(Event.is_test == False)
        .order_by(Catch.submitted_at)
    )
    catches_result = await db.execute(catches_query)
    catches = catches_result.scalars().all()

    # Trigger catch_approved for each catch to check special achievements
    from datetime import timedelta
    max_length_seen = 0.0  # Track max length for personal best detection

    for catch in catches:
        # Get event's format code
        event = await db.get(Event, catch.event_id)
        if event and event.event_type:
            format_code = "sf" if event.event_type.code == "street_fishing" else "ta"

            # Build context for fish species and special achievements
            catch_time = catch.catch_time or catch.submitted_at

            # Early bird: first 30 minutes
            early_cutoff = event.start_date + timedelta(minutes=30)
            is_early_bird = catch_time <= early_cutoff if catch_time and event.start_date else False

            # Last minute: final 30 minutes
            late_cutoff = event.end_date - timedelta(minutes=30)
            is_last_minute = catch_time >= late_cutoff if catch_time and event.end_date else False

            # Personal best detection (check if this was largest at time of catch)
            is_personal_best = catch.length > max_length_seen if catch.length else False
            if catch.length and catch.length > max_length_seen:
                max_length_seen = catch.length

            context = {
                "catch_length": catch.length,
                "catch_weight": catch.weight,
                "fish_id": catch.fish_id,
                "fish_slug": catch.fish.slug if catch.fish else None,
                "is_early_bird": is_early_bird,
                "is_last_minute": is_last_minute,
                "is_personal_best": is_personal_best,
            }

            awarded = await AchievementService.check_and_award_achievements(
                db,
                user_id=user_id,
                trigger="catch_approved",
                event_id=catch.event_id,
                catch_id=catch.id,
                context=context,
                format_code=format_code,
            )
            for ach in awarded:
                if ach.code not in new_achievements:
                    new_achievements.append(ach.code)
                    # Send notification for newly awarded achievement
                    from app.tasks.achievements import send_achievement_notification
                    send_achievement_notification.delay(user_id, ach.id, catch.event_id)

    # Trigger event_completed for each completed event
    for event_row in events:
        event = await db.get(Event, event_row.id)
        if event and event.event_type:
            format_code = "sf" if event.event_type.code == "street_fishing" else "ta"
            awarded = await AchievementService.check_and_award_achievements(
                db,
                user_id=user_id,
                trigger="event_completed",
                event_id=event_row.id,
                format_code=format_code,
            )
            for ach in awarded:
                if ach.code not in new_achievements:
                    new_achievements.append(ach.code)
                    # Send notification for newly awarded achievement
                    from app.tasks.achievements import send_achievement_notification
                    send_achievement_notification.delay(user_id, ach.id, event_row.id)

    # Check Hall of Fame achievements (SF/TA Champion)
    from app.tasks.achievements import _check_hall_of_fame_achievements
    hof_awards = await _check_hall_of_fame_achievements(db, user_id)
    for ach in hof_awards:
        if ach.code not in new_achievements:
            new_achievements.append(ach.code)
            from app.tasks.achievements import send_achievement_notification
            send_achievement_notification.delay(user_id, ach.id, None)

    await db.commit()

    # Get final achievement count
    after_count_result = await db.execute(
        select(func.count(UserAchievement.id)).where(UserAchievement.user_id == user_id)
    )
    achievements_after = after_count_result.scalar() or 0

    return AchievementRecalculateResponse(
        success=True,
        message=f"Achievement recalculation complete. {len(new_achievements)} new achievements awarded.",
        user_id=user_id,
        achievements_before=achievements_before,
        achievements_after=achievements_after,
        new_achievements=new_achievements,
    )


class BulkAchievementRecalculateRequest(BaseModel):
    """Request for bulk achievement recalculation."""
    send_notifications: bool = False


class BulkAchievementRecalculateResponse(BaseModel):
    """Response for bulk achievement recalculation."""
    success: bool
    message: str
    users_queued: int


@router.post("/recalculate-all-achievements", response_model=BulkAchievementRecalculateResponse)
async def recalculate_all_achievements(
    request: BulkAchievementRecalculateRequest = BulkAchievementRecalculateRequest(),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> BulkAchievementRecalculateResponse:
    """
    Trigger bulk achievement recalculation for ALL users.

    This will queue a background task that:
    1. Finds all users who participated in completed events
    2. Recalculates achievements for each user
    3. Optionally sends notifications for newly awarded achievements

    Use this after:
    - Achievement logic changes (new achievements, fixed bugs)
    - Database migrations affecting achievements
    - Data corrections

    Note: This is a long-running task. Users will be processed in the background
    with a 2-second delay between each to avoid overwhelming the system.
    """
    from sqlalchemy import distinct
    from app.models.event import EventEnrollment

    # Count users to be processed
    result = await db.execute(
        select(func.count(distinct(EventEnrollment.user_id)))
        .join(Event, Event.id == EventEnrollment.event_id)
        .where(EventEnrollment.status == "approved")
        .where(Event.status == "completed")
        .where(Event.is_test == False)
    )
    user_count = result.scalar() or 0

    # Queue the bulk task
    from app.tasks.achievements import recalculate_all_achievements as recalc_task
    recalc_task.delay(send_notifications=request.send_notifications)

    return BulkAchievementRecalculateResponse(
        success=True,
        message=f"Bulk achievement recalculation queued for {user_count} users. Processing in background.",
        users_queued=user_count,
    )
