"""Minigame schemas for request/response validation."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class MinigameScoreCreate(BaseModel):
    """Schema for creating a new minigame score."""

    score: int = Field(..., ge=0, description="Score achieved in the game")
    fish_caught: Optional[str] = Field(
        None, description="JSON array of fish caught during session"
    )
    duration_seconds: int = Field(
        ..., ge=0, description="Game session duration in seconds"
    )


class MinigameScoreResponse(BaseModel):
    """Schema for minigame score response."""

    id: int
    user_id: int
    score: int
    fish_caught: Optional[str] = None
    duration_seconds: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MinigameScoreWithUserResponse(BaseModel):
    """Schema for minigame score with user info (for leaderboard)."""

    id: int
    user_id: int
    user_name: str
    user_avatar_url: Optional[str] = None
    score: int
    fish_caught: Optional[str] = None
    duration_seconds: int
    created_at: datetime
    rank: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class MinigameScoreListResponse(BaseModel):
    """Schema for paginated list of minigame scores."""

    scores: List[MinigameScoreResponse]
    total: int
    page: int
    page_size: int


class MinigameLeaderboardResponse(BaseModel):
    """Schema for leaderboard response."""

    leaderboard: List[MinigameScoreWithUserResponse]
    personal_best: Optional[MinigameScoreResponse] = None
