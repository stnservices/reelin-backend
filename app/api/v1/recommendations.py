"""Recommendations API endpoints for personalized suggestions."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount
from app.schemas.recommendation import (
    AnglerRecommendation,
    AnglerRecommendationsResponse,
    DismissRequest,
    DismissResponse,
    EventRecommendation,
    EventRecommendationsResponse,
    EventSummary,
    UserSummary,
)
from app.services.recommendations_service import RecommendationsService
from app.services.redis_cache import redis_cache
from app.api.v1.pro import is_user_pro

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

# Cache TTL: 5 minutes
CACHE_TTL = 300


def _event_to_summary(event) -> EventSummary:
    """Convert Event model to EventSummary schema."""
    return EventSummary(
        id=event.id,
        name=event.name,
        slug=event.slug,
        start_date=event.start_date,
        end_date=event.end_date,
        location_name=event.location.name if event.location else event.location_name,
        cover_image_url=event.cover_image_url,
        event_type_name=event.event_type.name if event.event_type else None,
    )


@router.get("/events", response_model=EventRecommendationsResponse)
async def get_event_recommendations(
    limit: int = Query(10, ge=1, le=50, description="Max recommendations to return"),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get personalized event recommendations.

    Recommendations are based on:
    - Location proximity
    - Event types you've participated in
    - Fish species you've caught
    - Friends who are joining (Pro only)
    - Event popularity

    **Free users**: Top 3 recommendations
    **Pro users**: Full list with friends indicator
    """
    is_pro = await is_user_pro(current_user.id, db)

    # Try cache first
    cache_key = f"recommendations:events:{current_user.id}:{is_pro}"
    cached = await redis_cache.get(cache_key)
    if cached:
        # Apply limit to cached results
        recs = cached.get("recommendations", [])
        if not is_pro:
            recs = recs[:3]
        else:
            recs = recs[:limit]
        return EventRecommendationsResponse(
            recommendations=recs,
            is_pro=is_pro,
            total_available=len(recs),
        )

    # Generate recommendations
    service = RecommendationsService(db)
    scored_events = await service.get_event_recommendations(
        user=current_user,
        is_pro=is_pro,
        limit=limit,
    )

    # Convert to response format
    recommendations = [
        EventRecommendation(
            event=_event_to_summary(item["event"]),
            score=item["score"],
            reasons=item["reasons"],
            friends_enrolled=[
                UserSummary(**f) for f in (item["friends_enrolled"] or [])
            ] if item.get("friends_enrolled") else None,
        )
        for item in scored_events
    ]

    # Cache the results
    cache_data = {
        "recommendations": [r.model_dump() for r in recommendations],
    }
    await redis_cache.set(cache_key, cache_data, CACHE_TTL)

    return EventRecommendationsResponse(
        recommendations=recommendations,
        is_pro=is_pro,
        total_available=len(recommendations),
    )


@router.get("/anglers", response_model=AnglerRecommendationsResponse)
async def get_angler_recommendations(
    limit: int = Query(10, ge=1, le=50, description="Max recommendations to return"),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get personalized angler recommendations.

    Recommendations are based on:
    - Events you've both attended
    - Mutual connections (Pro only shows names)
    - Similar species caught
    - Location proximity
    - Similar experience level

    **Free users**: Top 3 recommendations
    **Pro users**: Full list with mutual friends details
    """
    is_pro = await is_user_pro(current_user.id, db)

    # Try cache first
    cache_key = f"recommendations:anglers:{current_user.id}:{is_pro}"
    cached = await redis_cache.get(cache_key)
    if cached:
        # Apply limit to cached results
        recs = cached.get("recommendations", [])
        if not is_pro:
            recs = recs[:3]
        else:
            recs = recs[:limit]
        return AnglerRecommendationsResponse(
            recommendations=recs,
            is_pro=is_pro,
            total_available=len(recs),
        )

    # Generate recommendations
    service = RecommendationsService(db)
    scored_anglers = await service.get_angler_recommendations(
        user=current_user,
        is_pro=is_pro,
        limit=limit,
    )

    # Convert to response format
    recommendations = [
        AnglerRecommendation(
            user=UserSummary(**item["user"]),
            score=item["score"],
            reasons=item["reasons"],
            mutual_friends=[
                UserSummary(**f) for f in (item["mutual_friends"] or [])
            ] if item.get("mutual_friends") else None,
        )
        for item in scored_anglers
    ]

    # Cache the results
    cache_data = {
        "recommendations": [r.model_dump() for r in recommendations],
    }
    await redis_cache.set(cache_key, cache_data, CACHE_TTL)

    return AnglerRecommendationsResponse(
        recommendations=recommendations,
        is_pro=is_pro,
        total_available=len(recommendations),
    )


@router.post("/dismiss", response_model=DismissResponse)
async def dismiss_recommendation(
    request: DismissRequest,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Dismiss a recommendation so it won't appear again.

    Use this when a user explicitly closes or ignores a recommendation.
    """
    service = RecommendationsService(db)
    await service.dismiss_recommendation(
        user_id=current_user.id,
        item_type=request.item_type,
        item_id=request.item_id,
    )

    # Invalidate cache
    is_pro = await is_user_pro(current_user.id, db)
    cache_key = f"recommendations:{request.item_type}s:{current_user.id}:{is_pro}"
    await redis_cache.delete(cache_key)

    return DismissResponse(status="dismissed")
