"""Recommendations API endpoints for personalized suggestions."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.core.permissions import AdminOnly
from app.models.user import UserAccount
from app.schemas.recommendation import (
    AnglerRecommendation,
    AnglerRecommendationsResponse,
    CompletedEventInsight,
    CompletedEventInsightsResponse,
    CompletedEventSummary,
    DismissRequest,
    DismissResponse,
    EventRecommendation,
    EventRecommendationsResponse,
    EventSummary,
    MLInsights,
    ReasonItem,
    UserSearchResponse,
    UserSearchResult,
    UserSummary,
)
from app.services.recommendations_service import RecommendationsService
from app.services.redis_cache import redis_cache
from app.api.v1.pro import is_user_pro

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

# Cache TTL: 5 minutes
CACHE_TTL = 300


def _redact_email(email: str) -> str:
    """Redact email for privacy: show first 2 chars + ***@domain."""
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        return f"{local[0]}***@{domain}"
    return f"{local[:2]}***@{domain}"


def _event_to_summary(event) -> EventSummary:
    """Convert Event model to EventSummary schema."""
    return EventSummary(
        id=event.id,
        name=event.name,
        slug=event.slug,
        start_date=event.start_date,
        end_date=event.end_date,
        location_name=event.location.name if event.location else event.location_name,
        cover_image_url=event.image_url,
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
            reasons=[ReasonItem(**r) for r in item["reasons"]],
            friends_enrolled=[
                UserSummary(**f) for f in (item["friends_enrolled"] or [])
            ] if item.get("friends_enrolled") else None,
            ml_insights=MLInsights(
                confidence=item["ml_insights"]["confidence"],
                confidence_label=item["ml_insights"]["confidence_label"],
                factors=[ReasonItem(**f) for f in item["ml_insights"]["factors"]],
            ) if item.get("ml_insights") else None,
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


@router.get("/my-events/insights", response_model=CompletedEventInsightsResponse)
async def get_my_event_insights(
    limit: int = Query(20, ge=1, le=50, description="Max events to analyze"),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get ML match insights for your completed events.

    Shows how well you matched each event you participated in:
    - **match_score**: Your match percentage (0-100)
    - **match_label**: Human-readable level (Very High, High, etc.)
    - **factors**: Why you were a good match for this event

    This helps you understand which types of events suit your profile.
    """
    service = RecommendationsService(db)
    insights_data = await service.get_completed_event_insights(
        user=current_user,
        limit=limit,
    )

    # Convert to response format
    insights = [
        CompletedEventInsight(
            event=CompletedEventSummary(
                id=item["event"]["id"],
                name=item["event"]["name"],
                slug=item["event"]["slug"],
                start_date=item["event"]["start_date"],
                end_date=item["event"]["end_date"],
                event_type_name=item["event"]["event_type_name"],
            ),
            match_score=item["match_score"],
            match_label=item["match_label"],
            factors=item["factors"],
        )
        for item in insights_data
    ]

    return CompletedEventInsightsResponse(
        insights=insights,
        total=len(insights),
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
            reasons=[ReasonItem(**r) for r in item["reasons"]],
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


@router.get("/anglers/search", response_model=UserSearchResponse)
async def search_anglers(
    q: str = Query(..., min_length=2, max_length=100, description="Search query (min 2 chars)"),
    limit: int = Query(20, ge=1, le=50, description="Max results to return"),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Search for anglers by name (PRO feature only).

    - Minimum 2 characters required
    - Case-insensitive search
    - Returns public profiles only
    - Shows if you're already following each user

    **Requires Pro subscription**
    """
    # Check PRO status
    is_pro = await is_user_pro(current_user.id, db)
    if not is_pro:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=403,
            detail="User search is a Pro feature. Upgrade to search for anglers.",
        )

    # Perform search
    service = RecommendationsService(db)
    results = await service.search_users(
        query=q,
        current_user_id=current_user.id,
        limit=limit,
    )

    return UserSearchResponse(
        results=[UserSearchResult(**r) for r in results],
        query=q,
        total=len(results),
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


@router.get("/debug/ml")
async def get_ml_debug_info(
    current_user: UserAccount = Depends(AdminOnly),
    db: AsyncSession = Depends(get_db),
):
    """
    Admin endpoint to view ML model debug information.

    Returns:
    - Active model metadata
    - Recent predictions
    - Feature importance
    """
    # Get active model info
    model_info = await db.execute(text("""
        SELECT id, name, model_type, file_path, trained_at,
               training_samples, positive_rate, roc_auc, cv_roc_auc,
               feature_columns, feature_importance, notes
        FROM ml_models
        WHERE is_active = true AND model_type = 'event_recommendations'
        ORDER BY created_at DESC
        LIMIT 1
    """))
    model = model_info.first()

    # Get recent predictions
    predictions_query = await db.execute(text("""
        SELECT user_id, entity_id, entity_type, prediction_score, prediction_ms, created_at
        FROM ml_prediction_logs
        WHERE entity_type = 'event'
        ORDER BY created_at DESC
        LIMIT 20
    """))
    predictions = predictions_query.all()

    return {
        "model": {
            "id": model.id if model else None,
            "name": model.name if model else None,
            "trained_at": model.trained_at.isoformat() if model and model.trained_at else None,
            "training_samples": model.training_samples if model else None,
            "positive_rate": float(model.positive_rate) if model and model.positive_rate else None,
            "roc_auc": float(model.roc_auc) if model and model.roc_auc else None,
            "cv_roc_auc": float(model.cv_roc_auc) if model and model.cv_roc_auc else None,
            "feature_columns": model.feature_columns if model else None,
            "feature_importance": model.feature_importance if model else None,
            "notes": model.notes if model else None,
        } if model else None,
        "recent_predictions": [
            {
                "user_id": p.user_id,
                "event_id": p.entity_id,
                "prediction_score": float(p.prediction_score) if p.prediction_score else None,
                "prediction_ms": float(p.prediction_ms) if p.prediction_ms else None,
                "confidence_label": (
                    "Very High" if p.prediction_score and p.prediction_score >= 0.9 else
                    "High" if p.prediction_score and p.prediction_score >= 0.7 else
                    "Moderate" if p.prediction_score and p.prediction_score >= 0.5 else
                    "Low" if p.prediction_score and p.prediction_score >= 0.3 else
                    "Very Low"
                ),
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in predictions
        ],
    }


@router.get("/debug/ml/user/{user_id}")
async def get_ml_debug_for_user(
    user_id: int,
    current_user: UserAccount = Depends(AdminOnly),
    db: AsyncSession = Depends(get_db),
):
    """
    Admin endpoint to view ML predictions for a specific user.

    Shows all recent predictions made for this user with full feature details.
    """
    # Get user info
    user_query = await db.execute(text("""
        SELECT ua.id, ua.email, up.full_name
        FROM user_accounts ua
        JOIN user_profiles up ON up.user_id = ua.id
        WHERE ua.id = :user_id
    """), {"user_id": user_id})
    user_info = user_query.first()

    if not user_info:
        return {"error": "User not found"}

    # Get user stats
    stats_query = await db.execute(text("""
        SELECT total_events, total_catches, unique_species_count,
               largest_catch_cm, total_wins, podium_finishes
        FROM user_event_type_stats
        WHERE user_id = :user_id AND event_type_id IS NULL
    """), {"user_id": user_id})
    stats = stats_query.first()

    # Get recent predictions for this user
    predictions_query = await db.execute(text("""
        SELECT entity_id, prediction_score, prediction_ms, created_at
        FROM ml_prediction_logs
        WHERE user_id = :user_id AND entity_type = 'event'
        ORDER BY created_at DESC
        LIMIT 30
    """), {"user_id": user_id})
    predictions = predictions_query.all()

    return {
        "user": {
            "id": user_info.id,
            "email": _redact_email(user_info.email),
            "name": user_info.full_name,
        },
        "stats": {
            "total_events": stats.total_events if stats else 0,
            "total_catches": stats.total_catches if stats else 0,
            "unique_species_count": stats.unique_species_count if stats else 0,
            "largest_catch_cm": float(stats.largest_catch_cm) if stats and stats.largest_catch_cm else 0,
            "total_wins": stats.total_wins if stats else 0,
            "podium_finishes": stats.podium_finishes if stats else 0,
        } if stats else None,
        "predictions": [
            {
                "event_id": p.entity_id,
                "prediction_score": float(p.prediction_score) if p.prediction_score else None,
                "prediction_ms": float(p.prediction_ms) if p.prediction_ms else None,
                "confidence_label": (
                    "Very High" if p.prediction_score and p.prediction_score >= 0.9 else
                    "High" if p.prediction_score and p.prediction_score >= 0.7 else
                    "Moderate" if p.prediction_score and p.prediction_score >= 0.5 else
                    "Low" if p.prediction_score and p.prediction_score >= 0.3 else
                    "Very Low"
                ),
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in predictions
        ],
    }
