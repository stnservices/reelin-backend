"""Minigame endpoints for fishing minigame scores."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.minigame import MinigameScore
from app.models.user import UserAccount, UserProfile
from app.schemas.minigame import (
    MinigameScoreCreate,
    MinigameScoreResponse,
    MinigameScoreWithUserResponse,
    MinigameScoreListResponse,
    MinigameLeaderboardResponse,
)

router = APIRouter()


@router.get("/scores", response_model=MinigameScoreListResponse)
async def get_my_scores(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=50, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get current user's minigame scores.
    Returns scores ordered by score descending (highest first).
    """
    # Count total
    count_query = select(func.count(MinigameScore.id)).where(
        MinigameScore.user_id == current_user.id
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get paginated scores
    offset = (page - 1) * page_size
    query = (
        select(MinigameScore)
        .where(MinigameScore.user_id == current_user.id)
        .order_by(desc(MinigameScore.score))
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(query)
    scores = result.scalars().all()

    return MinigameScoreListResponse(
        scores=[MinigameScoreResponse.model_validate(s) for s in scores],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/scores", response_model=MinigameScoreResponse, status_code=status.HTTP_201_CREATED)
async def create_score(
    score_data: MinigameScoreCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Submit a new minigame score.
    Called after each game session ends.
    """
    new_score = MinigameScore(
        user_id=current_user.id,
        score=score_data.score,
        fish_caught=score_data.fish_caught,
        duration_seconds=score_data.duration_seconds,
    )
    db.add(new_score)
    await db.commit()
    await db.refresh(new_score)

    return MinigameScoreResponse.model_validate(new_score)


@router.get("/scores/leaderboard", response_model=MinigameLeaderboardResponse)
async def get_leaderboard(
    limit: int = Query(10, ge=1, le=50, description="Number of top scores to return"),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get global leaderboard of top scores.
    Returns top N scores with user information and current user's personal best.
    """
    # Get top scores with user info
    query = (
        select(
            MinigameScore,
            UserProfile.first_name,
            UserProfile.last_name,
            UserAccount.avatar_url,
            UserProfile.profile_picture_url,
        )
        .join(UserAccount, MinigameScore.user_id == UserAccount.id)
        .join(UserProfile, UserProfile.user_id == UserAccount.id)
        .order_by(desc(MinigameScore.score))
        .limit(limit)
    )
    result = await db.execute(query)
    rows = result.all()

    leaderboard = []
    for idx, row in enumerate(rows, start=1):
        score = row[0]
        first_name = row[1]
        last_name = row[2]
        avatar_url = row[3]
        profile_picture_url = row[4]

        leaderboard.append(
            MinigameScoreWithUserResponse(
                id=score.id,
                user_id=score.user_id,
                user_name=f"{first_name} {last_name}",
                user_avatar_url=profile_picture_url or avatar_url,
                score=score.score,
                fish_caught=score.fish_caught,
                duration_seconds=score.duration_seconds,
                created_at=score.created_at,
                rank=idx,
            )
        )

    # Get current user's personal best
    personal_best_query = (
        select(MinigameScore)
        .where(MinigameScore.user_id == current_user.id)
        .order_by(desc(MinigameScore.score))
        .limit(1)
    )
    personal_best_result = await db.execute(personal_best_query)
    personal_best = personal_best_result.scalar_one_or_none()

    return MinigameLeaderboardResponse(
        leaderboard=leaderboard,
        personal_best=MinigameScoreResponse.model_validate(personal_best) if personal_best else None,
    )
