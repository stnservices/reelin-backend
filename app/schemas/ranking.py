"""Schemas for Top Anglers rankings."""

from typing import List, Optional

from pydantic import BaseModel, Field


class TopAnglerResponse(BaseModel):
    """Response schema for a single Top Angler entry."""

    rank: int = Field(..., description="Position in the ranking")
    user_id: int = Field(..., description="User ID")
    user_name: str = Field(..., description="Display name")
    avatar_url: Optional[str] = None

    # Scoring
    total_score: int = Field(..., description="Total score (position + podium + participation)")
    position_points: int = Field(..., description="Sum of position points")
    podium_bonus: int = Field(..., description="Sum of podium bonuses")
    participation_weight: int = Field(..., description="Participation points (count × 3)")
    participations: int = Field(..., description="Number of national events")

    # Tiebreakers
    total_leaderboard_points: float = Field(..., description="Sum of leaderboard points from all events")
    avg_catches_per_event: float = Field(..., description="Average validated catches per event")
    best_single_catch: float = Field(..., description="Best single catch length (cm)")

    # Medals
    gold_count: int = Field(0, description="Number of 1st place finishes")
    silver_count: int = Field(0, description="Number of 2nd place finishes")
    bronze_count: int = Field(0, description="Number of 3rd place finishes")


class TopAnglersListResponse(BaseModel):
    """Response schema for Top Anglers ranking list."""

    anglers: List[TopAnglerResponse]
    format_code: str = Field(..., description="Format filter: sf, ta, or all")
    year: Optional[int] = Field(None, description="Year filter, null = all-time")
    total_participants: int = Field(..., description="Total anglers in ranking")
    available_years: List[int] = Field(default_factory=list, description="Years with national event data")


class ScoringFormulaResponse(BaseModel):
    """Response schema explaining the scoring formula."""

    formula: str = "Score = Position Points + Podium Bonus + Participation Points"
    position_points: dict = {
        "1st": 100, "2nd": 85, "3rd": 70, "4th": 60, "5th": 50,
        "6th": 42, "7th": 36, "8th": 30, "9th": 25, "10th": 20,
        "11th-20th": "15-6 (decreasing)", "21st+": 5
    }
    podium_bonus: dict = {
        "Gold (1st)": "+25",
        "Silver (2nd)": "+15",
        "Bronze (3rd)": "+10"
    }
    participation_points: str = "+3 points per national event"
    tiebreakers: List[str] = [
        "1. Total Score (primary)",
        "2. Sum of leaderboard points from all national events",
        "3. Average validated catches per event",
        "4. Best single catch length"
    ]
