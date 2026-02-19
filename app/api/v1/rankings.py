"""Top Anglers rankings endpoints for mobile app."""

from typing import Optional, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import UserAccount
from app.schemas.ranking import (
    TopAnglerResponse,
    TopAnglersListResponse,
    ScoringFormulaResponse,
)

router = APIRouter()


@router.get("/top-anglers", response_model=TopAnglersListResponse)
async def get_top_anglers(
    db: AsyncSession = Depends(get_db),
    format_code: Optional[str] = Query(None, pattern="^(sf|ta)$", description="Filter by format: sf or ta"),
    year: Optional[int] = Query(None, ge=2000, le=2100, description="Filter by year, or null for all-time"),
    limit: int = Query(10, ge=1, le=100, description="Number of results"),
) -> TopAnglersListResponse:
    """
    Get Top Anglers ranking.

    The ranking is calculated from national events (is_national_event=true) with:
    - Position Points based on finishing position
    - Podium Bonus for 1st, 2nd, 3rd place finishes
    - Participation Weight (+3 per national event)

    Tiebreakers (in order):
    1. Total Score
    2. Sum of leaderboard points from all events
    3. Average validated catches per event
    4. Best single catch length

    Public endpoint for mobile app.
    """
    # Build the query based on filters
    # Note: user_profiles has first_name/last_name, user_accounts has just email
    if year is None:
        # All-time ranking from the aggregated view
        if format_code:
            # Filter by format
            query = text("""
                SELECT
                    r.user_id,
                    p.first_name || ' ' || p.last_name as user_name,
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
                WHERE r.format_code = :format_code
                ORDER BY
                    r.total_score DESC,
                    r.total_leaderboard_points DESC,
                    r.avg_catches_per_event DESC,
                    r.best_single_catch DESC
                LIMIT :limit
            """)
            params = {"format_code": format_code, "limit": limit}
        else:
            # Overall (aggregate all formats)
            query = text("""
                SELECT
                    r.user_id,
                    p.first_name || ' ' || p.last_name as user_name,
                    p.profile_picture_url as avatar_url,
                    SUM(r.participations)::integer as participations,
                    SUM(r.total_position_points)::integer as position_points,
                    SUM(r.total_podium_bonus)::integer as podium_bonus,
                    SUM(r.participation_weight)::integer as participation_weight,
                    SUM(r.total_score)::integer as total_score,
                    SUM(r.total_leaderboard_points)::float as total_leaderboard_points,
                    AVG(r.avg_catches_per_event)::float as avg_catches_per_event,
                    MAX(r.best_single_catch)::float as best_single_catch,
                    SUM(r.gold_count)::integer as gold_count,
                    SUM(r.silver_count)::integer as silver_count,
                    SUM(r.bronze_count)::integer as bronze_count
                FROM top_anglers_all_time r
                JOIN user_profiles p ON p.user_id = r.user_id
                GROUP BY r.user_id, p.first_name, p.last_name, p.profile_picture_url
                ORDER BY
                    SUM(r.total_score) DESC,
                    SUM(r.total_leaderboard_points) DESC,
                    AVG(r.avg_catches_per_event) DESC,
                    MAX(r.best_single_catch) DESC
                LIMIT :limit
            """)
            params = {"limit": limit}
    else:
        # Specific year ranking
        if format_code:
            query = text("""
                SELECT
                    r.user_id,
                    p.first_name || ' ' || p.last_name as user_name,
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
                WHERE r.format_code = :format_code AND r.competition_year = :year
                ORDER BY
                    r.total_score DESC,
                    r.total_leaderboard_points DESC,
                    r.avg_catches_per_event DESC,
                    r.best_single_catch DESC
                LIMIT :limit
            """)
            params = {"format_code": format_code, "year": year, "limit": limit}
        else:
            # Overall for specific year (aggregate all formats)
            query = text("""
                SELECT
                    r.user_id,
                    p.first_name || ' ' || p.last_name as user_name,
                    p.profile_picture_url as avatar_url,
                    SUM(r.participations)::integer as participations,
                    SUM(r.total_position_points)::integer as position_points,
                    SUM(r.total_podium_bonus)::integer as podium_bonus,
                    SUM(r.participation_weight)::integer as participation_weight,
                    SUM(r.total_score)::integer as total_score,
                    SUM(r.total_leaderboard_points)::float as total_leaderboard_points,
                    AVG(r.avg_catches_per_event)::float as avg_catches_per_event,
                    MAX(r.best_single_catch)::float as best_single_catch,
                    SUM(r.gold_count)::integer as gold_count,
                    SUM(r.silver_count)::integer as silver_count,
                    SUM(r.bronze_count)::integer as bronze_count
                FROM top_anglers_ranking r
                JOIN user_profiles p ON p.user_id = r.user_id
                WHERE r.competition_year = :year
                GROUP BY r.user_id, p.first_name, p.last_name, p.profile_picture_url
                ORDER BY
                    SUM(r.total_score) DESC,
                    SUM(r.total_leaderboard_points) DESC,
                    AVG(r.avg_catches_per_event) DESC,
                    MAX(r.best_single_catch) DESC
                LIMIT :limit
            """)
            params = {"year": year, "limit": limit}

    result = await db.execute(query, params)
    rows = result.fetchall()

    # Get available years from national events
    years_query = text("""
        SELECT DISTINCT EXTRACT(YEAR FROM start_date)::integer as year
        FROM events
        WHERE is_national_event = TRUE AND status = 'completed' AND is_test = FALSE
        ORDER BY year DESC
    """)
    years_result = await db.execute(years_query)
    available_years = [row.year for row in years_result.fetchall()]

    # Build response
    anglers: List[TopAnglerResponse] = []
    for rank, row in enumerate(rows, start=1):
        anglers.append(TopAnglerResponse(
            rank=rank,
            user_id=row.user_id,
            user_name=row.user_name,
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

    return TopAnglersListResponse(
        anglers=anglers,
        format_code=format_code or "all",
        year=year,
        total_participants=len(anglers),
        available_years=available_years,
    )


@router.get("/scoring-formula", response_model=ScoringFormulaResponse)
async def get_scoring_formula() -> ScoringFormulaResponse:
    """
    Get the scoring formula explanation.

    Public endpoint for the info icon (i) in the mobile app.
    """
    return ScoringFormulaResponse()
