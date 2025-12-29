"""Achievements and statistics endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount
from app.services.achievement_service import achievement_service, AchievementService
from app.services.statistics_service import statistics_service, StatisticsService
from app.schemas.achievement import (
    AchievementDefinitionResponse,
    UserAchievementResponse,
    AchievementProgressResponse,
    EventTypeStatsResponse,
    UserStatisticsResponse,
    UserAchievementsListResponse,
    AchievementGalleryResponse,
)

router = APIRouter()


@router.get("/me", response_model=UserAchievementsListResponse)
async def get_my_achievements(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get current user's achievements and progress.
    Returns earned achievements and progress toward tiered achievements.
    """
    data = await achievement_service.get_user_achievements(db, current_user.id)

    # Convert progress to response format
    progress_responses = []
    type_display_names = {
        "participation": "Events Participated",
        "catch_count": "Total Catches",
        "species_count": "Unique Species",
        "podium_count": "Podium Finishes",
        "win_count": "Event Wins",
    }
    tier_display_names = {
        "bronze": "Bronze",
        "silver": "Silver",
        "gold": "Gold",
        "platinum": "Platinum",
    }

    for p in data["progress"]:
        progress_responses.append(
            AchievementProgressResponse(
                achievement_type=p["achievement_type"],
                achievement_type_display=type_display_names.get(
                    p["achievement_type"], p["achievement_type"]
                ),
                current_tier=p["current_tier"],
                current_tier_name=tier_display_names.get(p["current_tier"])
                if p["current_tier"]
                else None,
                current_value=p["current_value"],
                next_tier=p["next_tier"],
                next_tier_name=tier_display_names.get(p["next_tier"])
                if p["next_tier"]
                else None,
                next_threshold=p["next_threshold"],
                progress_percentage=p["progress_percentage"],
            )
        )

    return UserAchievementsListResponse(
        earned_achievements=[
            UserAchievementResponse.from_model(a) for a in data["earned_achievements"]
        ],
        progress=progress_responses,
        total_earned=data["total_earned"],
        total_available=data["total_available"],
    )


@router.get("/users/{user_id}", response_model=UserAchievementsListResponse)
async def get_user_achievements(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get achievements for a specific user.
    Visible to all authenticated users (public profile data).
    """
    # Verify user exists
    user = await db.get(UserAccount, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    data = await achievement_service.get_user_achievements(db, user_id)

    # Convert progress to response format
    progress_responses = []
    type_display_names = {
        "participation": "Events Participated",
        "catch_count": "Total Catches",
        "species_count": "Unique Species",
        "podium_count": "Podium Finishes",
        "win_count": "Event Wins",
    }
    tier_display_names = {
        "bronze": "Bronze",
        "silver": "Silver",
        "gold": "Gold",
        "platinum": "Platinum",
    }

    for p in data["progress"]:
        progress_responses.append(
            AchievementProgressResponse(
                achievement_type=p["achievement_type"],
                achievement_type_display=type_display_names.get(
                    p["achievement_type"], p["achievement_type"]
                ),
                current_tier=p["current_tier"],
                current_tier_name=tier_display_names.get(p["current_tier"])
                if p["current_tier"]
                else None,
                current_value=p["current_value"],
                next_tier=p["next_tier"],
                next_tier_name=tier_display_names.get(p["next_tier"])
                if p["next_tier"]
                else None,
                next_threshold=p["next_threshold"],
                progress_percentage=p["progress_percentage"],
            )
        )

    return UserAchievementsListResponse(
        earned_achievements=[
            UserAchievementResponse.from_model(a) for a in data["earned_achievements"]
        ],
        progress=progress_responses,
        total_earned=data["total_earned"],
        total_available=data["total_available"],
    )


@router.get("/gallery", response_model=AchievementGalleryResponse)
async def get_achievement_gallery(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get all available achievements (badge gallery).
    Shows all achievements that can be earned.
    """
    data = await achievement_service.get_all_achievements(db)

    return AchievementGalleryResponse(
        tiered=[AchievementDefinitionResponse.from_model(a) for a in data["tiered"]],
        special=[AchievementDefinitionResponse.from_model(a) for a in data["special"]],
        total=data["total"],
    )


@router.get("/statistics/me", response_model=UserStatisticsResponse)
async def get_my_statistics(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get current user's statistics.
    Returns overall stats and per-event-type breakdown.
    """
    data = await statistics_service.get_user_statistics(db, current_user.id)

    return UserStatisticsResponse(
        overall=EventTypeStatsResponse.from_model(data["overall"]),
        by_event_type=[
            EventTypeStatsResponse.from_model(s) for s in data["by_event_type"]
        ],
    )


@router.get("/statistics/users/{user_id}", response_model=UserStatisticsResponse)
async def get_user_statistics(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get statistics for a specific user.
    Visible to all authenticated users (public profile data).
    """
    # Verify user exists
    user = await db.get(UserAccount, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    data = await statistics_service.get_user_statistics(db, user_id)

    return UserStatisticsResponse(
        overall=EventTypeStatsResponse.from_model(data["overall"]),
        by_event_type=[
            EventTypeStatsResponse.from_model(s) for s in data["by_event_type"]
        ],
    )


@router.post("/statistics/recalculate", status_code=status.HTTP_202_ACCEPTED)
async def recalculate_my_statistics(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Recalculate current user's statistics.
    Use if stats seem out of sync. Can be slow for users with many events.
    """
    await statistics_service.recalculate_all_stats(db, current_user.id)
    await db.commit()

    return {"message": "Statistics recalculated successfully"}
