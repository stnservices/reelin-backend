"""ML Predictions API endpoints for mobile app."""

from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount
from app.models.event import Event
from app.models.catch import Catch, CatchStatus
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.fish import Fish
from app.models.statistics import UserEventTypeStats
from app.models.hall_of_fame import HallOfFameEntry
from app.services.ml_service import MLService

router = APIRouter()


# Response schemas
class HourPrediction(BaseModel):
    """Prediction for a specific hour."""
    hour: int
    probability: float
    label: str  # "6 AM", "7 PM", etc.


class CatchTimeResponse(BaseModel):
    """Response for catch time predictions."""
    best_hours: List[HourPrediction]
    recommendation: str


class SpeciesPrediction(BaseModel):
    """Prediction for a species."""
    fish_id: int
    fish_name: str
    probability: float
    image_url: Optional[str] = None


class SpeciesForecastResponse(BaseModel):
    """Response for species forecast."""
    predictions: List[SpeciesPrediction]
    based_on: str  # "location", "season", "history"


class CompetitionContext(BaseModel):
    """Competition strength context for performance prediction."""
    enrolled_count: int
    hof_members_count: int
    world_champions_count: int
    national_champions_count: int
    avg_competitor_experience: float
    user_experience_percentile: float


class PerformancePrediction(BaseModel):
    """User performance prediction."""
    predicted_bracket: str  # winner, podium, top_10, other
    confidence: float
    message: str
    probabilities: dict
    competition: Optional[CompetitionContext] = None


class AttendancePrediction(BaseModel):
    """Event attendance prediction."""
    predicted_attendance: int
    confidence: str


def format_hour(hour: int) -> str:
    """Format hour as human-readable string."""
    if hour == 0:
        return "12 AM"
    elif hour < 12:
        return f"{hour} AM"
    elif hour == 12:
        return "12 PM"
    else:
        return f"{hour - 12} PM"


@router.get("/events/{event_id}/optimal-times", response_model=CatchTimeResponse)
async def get_optimal_catch_times(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> CatchTimeResponse:
    """
    Get optimal fishing times for a user in an event.
    Based on user's historical catch patterns and ML model.
    """
    # Verify event exists
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get user's catch history for features
    stmt = select(func.count()).where(
        Catch.user_id == current_user.id,
        Catch.status == CatchStatus.APPROVED.value,
    )
    result = await db.execute(stmt)
    user_total_catches = result.scalar() or 0

    # Get user's preferred hours (hours where they've caught most)
    # Convert UTC to Romania timezone before extracting hour
    local_time = func.timezone('Europe/Bucharest', Catch.catch_time)
    hour_stmt = (
        select(
            func.extract('hour', local_time).label('hour'),
            func.count().label('count')
        )
        .where(
            Catch.user_id == current_user.id,
            Catch.status == CatchStatus.APPROVED.value,
            Catch.catch_time.isnot(None),
        )
        .group_by(func.extract('hour', local_time))
        .order_by(func.count().desc())
        .limit(3)
    )
    hour_result = await db.execute(hour_stmt)
    preferred_hours = [int(row.hour) for row in hour_result.all()]

    # Build features
    now = datetime.now(timezone.utc)
    features = {
        "day_of_week": event.start_date.weekday() if event.start_date else now.weekday(),
        "month": event.start_date.month if event.start_date else now.month,
        "fish_id": 0,  # General prediction
        "has_location": 1 if event.location else 0,
        "user_total_catches": user_total_catches,
    }

    # Get ML predictions
    ml_service = MLService(db)
    predictions = await ml_service.predict_catch_time(current_user.id, features)

    if predictions:
        # Take top 6 hours
        top_hours = predictions[:6]
        best_hours = [
            HourPrediction(
                hour=p["hour"],
                probability=p["probability"],
                label=format_hour(p["hour"]),
            )
            for p in top_hours
        ]

        # Generate recommendation
        top_hour = top_hours[0]["hour"]
        if 5 <= top_hour < 10:
            recommendation = "Early morning looks best for you!"
        elif 17 <= top_hour < 21:
            recommendation = "Evening hours are your sweet spot!"
        else:
            recommendation = f"Your best time is around {format_hour(top_hour)}"
    else:
        # Fallback to historical data
        if preferred_hours:
            best_hours = [
                HourPrediction(hour=h, probability=0.7, label=format_hour(h))
                for h in preferred_hours
            ]
            recommendation = "Based on your catch history"
        else:
            # Default recommendations
            best_hours = [
                HourPrediction(hour=6, probability=0.6, label="6 AM"),
                HourPrediction(hour=7, probability=0.55, label="7 AM"),
                HourPrediction(hour=18, probability=0.5, label="6 PM"),
            ]
            recommendation = "General best fishing times"

    return CatchTimeResponse(
        best_hours=best_hours,
        recommendation=recommendation,
    )


@router.get("/events/{event_id}/species-forecast", response_model=SpeciesForecastResponse)
async def get_species_forecast(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> SpeciesForecastResponse:
    """
    Predict which species a user is likely to catch in an event.
    Based on location, season, and user history.
    """
    # Verify event exists
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get user stats
    stats_stmt = select(UserEventTypeStats).where(
        UserEventTypeStats.user_id == current_user.id,
        UserEventTypeStats.event_type_id.is_(None),
    )
    stats_result = await db.execute(stats_stmt)
    user_stats = stats_result.scalar_one_or_none()

    # Get user's top species
    species_stmt = (
        select(Catch.fish_id, func.count().label('count'))
        .where(
            Catch.user_id == current_user.id,
            Catch.status == CatchStatus.APPROVED.value,
            Catch.fish_id.isnot(None),
        )
        .group_by(Catch.fish_id)
        .order_by(func.count().desc())
        .limit(1)
    )
    species_result = await db.execute(species_stmt)
    top_species_row = species_result.first()
    top_species = top_species_row.fish_id if top_species_row else 0

    # Build features
    now = datetime.now(timezone.utc)
    event_date = event.start_date or now
    features = {
        "hour": 8,  # Default morning
        "day_of_week": event_date.weekday(),
        "month": event_date.month,
        "lat_zone": round(event.location.latitude, 1) if event.location and event.location.latitude else 0,
        "lng_zone": round(event.location.longitude, 1) if event.location and event.location.longitude else 0,
        "has_location": 1 if event.location else 0,
        "user_total_catches": user_stats.total_catches if user_stats else 0,
        "user_unique_species": user_stats.unique_species_count if user_stats else 0,
        "user_caught_before": 0,
        "user_top_species": top_species,
        "is_spring": 1 if event_date.month in [3, 4, 5] else 0,
        "is_summer": 1 if event_date.month in [6, 7, 8] else 0,
        "is_autumn": 1 if event_date.month in [9, 10, 11] else 0,
        "is_winter": 1 if event_date.month in [12, 1, 2] else 0,
        "is_morning": 1,
        "is_evening": 0,
    }

    # Get ML predictions
    ml_service = MLService(db)
    predictions = await ml_service.predict_species(current_user.id, features, top_k=5)

    if predictions:
        # Fetch fish details
        fish_ids = [p["fish_id"] for p in predictions]
        fish_stmt = select(Fish).where(Fish.id.in_(fish_ids))
        fish_result = await db.execute(fish_stmt)
        fish_map = {f.id: f for f in fish_result.scalars().all()}

        species_predictions = []
        for p in predictions:
            fish = fish_map.get(p["fish_id"])
            if fish:
                species_predictions.append(SpeciesPrediction(
                    fish_id=fish.id,
                    fish_name=fish.name,
                    probability=p["probability"],
                    image_url=fish.image_url,
                ))

        based_on = "location and season" if event.location else "your history"
    else:
        # Fallback - get most common species from user's catches
        common_stmt = (
            select(Catch.fish_id, func.count().label('count'))
            .where(
                Catch.user_id == current_user.id,
                Catch.status == CatchStatus.APPROVED.value,
                Catch.fish_id.isnot(None),
            )
            .group_by(Catch.fish_id)
            .order_by(func.count().desc())
            .limit(5)
        )
        common_result = await db.execute(common_stmt)
        common_species = common_result.all()

        if common_species:
            fish_ids = [row.fish_id for row in common_species]
            fish_stmt = select(Fish).where(Fish.id.in_(fish_ids))
            fish_result = await db.execute(fish_stmt)
            fish_map = {f.id: f for f in fish_result.scalars().all()}

            species_predictions = []
            for row in common_species:
                fish = fish_map.get(row.fish_id)
                if fish:
                    species_predictions.append(SpeciesPrediction(
                        fish_id=fish.id,
                        fish_name=fish.name,
                        probability=0.5,
                        image_url=fish.image_url,
                    ))
            based_on = "your catch history"
        else:
            species_predictions = []
            based_on = "no data available"

    return SpeciesForecastResponse(
        predictions=species_predictions,
        based_on=based_on,
    )


@router.get("/events/{event_id}/my-prediction", response_model=PerformancePrediction)
async def get_my_performance_prediction(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> PerformancePrediction:
    """
    Get predicted performance bracket for user in an event.
    Returns winner/podium/top10/other prediction with competition context.
    """
    # Verify event exists
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get user stats
    stats_stmt = select(UserEventTypeStats).where(
        UserEventTypeStats.user_id == current_user.id,
        UserEventTypeStats.event_type_id.is_(None),
    )
    stats_result = await db.execute(stats_stmt)
    user_stats = stats_result.scalar_one_or_none()

    # Get user's Hall of Fame entries
    user_hof_stmt = select(HallOfFameEntry).where(HallOfFameEntry.user_id == current_user.id)
    user_hof_result = await db.execute(user_hof_stmt)
    user_hof_entries = user_hof_result.scalars().all()

    user_hof_count = len(user_hof_entries)
    user_hof_world = sum(1 for e in user_hof_entries if 'world' in (e.achievement_type or '').lower())
    user_hof_national = sum(1 for e in user_hof_entries if 'national' in (e.achievement_type or '').lower())
    user_hof_champion = sum(1 for e in user_hof_entries if 'champion' in (e.achievement_type or '').lower())

    # Calculate user win/podium rates
    user_total_events = user_stats.total_events if user_stats and user_stats.total_events else 0
    user_wins = user_stats.total_wins if user_stats else 0
    user_podiums = user_stats.podium_finishes if user_stats else 0
    user_win_rate = user_wins / user_total_events if user_total_events > 0 else 0
    user_podium_rate = user_podiums / user_total_events if user_total_events > 0 else 0

    # ============ Get Competition Features ============
    # Get enrolled users for this event
    enrollment_stmt = select(EventEnrollment.user_id).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.status == EnrollmentStatus.APPROVED.value,
    )
    enrollment_result = await db.execute(enrollment_stmt)
    enrolled_user_ids = [r[0] for r in enrollment_result.all()]

    # Exclude current user from competitors
    competitor_ids = [uid for uid in enrolled_user_ids if uid != current_user.id]
    enrolled_count = len(enrolled_user_ids)

    # Get competitor stats
    competitor_stats = {}
    if competitor_ids:
        comp_stats_stmt = select(UserEventTypeStats).where(
            UserEventTypeStats.user_id.in_(competitor_ids),
            UserEventTypeStats.event_type_id.is_(None),
        )
        comp_stats_result = await db.execute(comp_stats_stmt)
        for stat in comp_stats_result.scalars().all():
            competitor_stats[stat.user_id] = stat

    # Get competitor HOF entries
    competitor_hof = {}
    if competitor_ids:
        comp_hof_stmt = select(HallOfFameEntry).where(HallOfFameEntry.user_id.in_(competitor_ids))
        comp_hof_result = await db.execute(comp_hof_stmt)
        for entry in comp_hof_result.scalars().all():
            if entry.user_id not in competitor_hof:
                competitor_hof[entry.user_id] = []
            competitor_hof[entry.user_id].append(entry)

    # Calculate competition features
    if competitor_ids:
        competitor_win_rates = []
        competitor_experiences = []

        for cid in competitor_ids:
            cstats = competitor_stats.get(cid)
            if cstats and cstats.total_events:
                competitor_win_rates.append((cstats.total_wins or 0) / cstats.total_events)
                competitor_experiences.append(cstats.total_events)
            else:
                competitor_win_rates.append(0)
                competitor_experiences.append(0)

        enrolled_avg_win_rate = sum(competitor_win_rates) / len(competitor_win_rates) if competitor_win_rates else 0
        enrolled_max_win_rate = max(competitor_win_rates) if competitor_win_rates else 0
        enrolled_avg_events = sum(competitor_experiences) / len(competitor_experiences) if competitor_experiences else 0

        # Count HOF members among competitors
        enrolled_hof_count = sum(1 for cid in competitor_ids if len(competitor_hof.get(cid, [])) > 0)
        enrolled_world_champ_count = sum(
            sum(1 for e in competitor_hof.get(cid, []) if 'world_champion' in (e.achievement_type or '').lower())
            for cid in competitor_ids
        )
        enrolled_national_champ_count = sum(
            sum(1 for e in competitor_hof.get(cid, []) if 'national_champion' in (e.achievement_type or '').lower())
            for cid in competitor_ids
        )

        # Calculate user's percentile among enrolled
        users_with_less_exp = sum(1 for exp in competitor_experiences if exp < user_total_events)
        user_experience_percentile = (users_with_less_exp / len(competitor_ids)) * 100 if competitor_ids else 50

        user_vs_avg_win_rate = user_win_rate - enrolled_avg_win_rate
    else:
        enrolled_avg_win_rate = 0
        enrolled_max_win_rate = 0
        enrolled_avg_events = 0
        enrolled_hof_count = 0
        enrolled_world_champ_count = 0
        enrolled_national_champ_count = 0
        user_experience_percentile = 50
        user_vs_avg_win_rate = 0

    # Build features (includes both user and competition features)
    features = {
        # User features
        "user_total_events": user_total_events,
        "user_wins": user_wins,
        "user_podiums": user_podiums,
        "user_best_rank": user_stats.best_rank if user_stats and user_stats.best_rank else 100,
        "user_total_catches": user_stats.total_catches if user_stats else 0,
        "win_rate": user_win_rate,
        "podium_rate": user_podium_rate,
        "user_avg_catch_length": float(user_stats.average_catch_length) if user_stats and user_stats.average_catch_length else 0,
        "hof_entry_count": user_hof_count,
        "hof_world_count": user_hof_world,
        "hof_national_count": user_hof_national,
        "hof_champion_count": user_hof_champion,
        "is_hof_member": 1 if user_hof_count > 0 else 0,
        # Competition features
        "enrolled_count": enrolled_count,
        "enrolled_avg_win_rate": enrolled_avg_win_rate,
        "enrolled_max_win_rate": enrolled_max_win_rate,
        "enrolled_avg_events": enrolled_avg_events,
        "enrolled_hof_count": enrolled_hof_count,
        "enrolled_world_champ_count": enrolled_world_champ_count,
        "enrolled_national_champ_count": enrolled_national_champ_count,
        "user_experience_percentile": user_experience_percentile,
        "user_vs_avg_win_rate": user_vs_avg_win_rate,
    }

    # Build competition context for response
    competition_context = CompetitionContext(
        enrolled_count=enrolled_count,
        hof_members_count=enrolled_hof_count,
        world_champions_count=enrolled_world_champ_count,
        national_champions_count=enrolled_national_champ_count,
        avg_competitor_experience=enrolled_avg_events,
        user_experience_percentile=user_experience_percentile,
    )

    # Get ML prediction
    ml_service = MLService(db)
    prediction = await ml_service.predict_user_performance(features)

    if prediction:
        bracket = prediction["predicted_bracket"]
        confidence = prediction["confidence"]

        # Generate message based on prediction AND competition context
        if enrolled_world_champ_count > 0 or enrolled_national_champ_count > 0:
            champ_text = []
            if enrolled_world_champ_count > 0:
                champ_text.append(f"{enrolled_world_champ_count} world champion{'s' if enrolled_world_champ_count > 1 else ''}")
            if enrolled_national_champ_count > 0:
                champ_text.append(f"{enrolled_national_champ_count} national champion{'s' if enrolled_national_champ_count > 1 else ''}")
            competition_desc = " and ".join(champ_text)

            if bracket == "winner":
                message = f"Against {competition_desc}, you still have a chance to win! 🏆"
            elif bracket == "podium":
                message = f"Tough competition with {competition_desc}! Podium is still possible! 🥇🥈🥉"
            elif bracket == "top_10":
                message = f"Strong field with {competition_desc}. Top 10 is a solid goal! 💪"
            else:
                message = f"Facing {competition_desc} - great learning experience! 🎣"
        else:
            if bracket == "winner":
                message = "You have a strong chance to win! 🏆"
            elif bracket == "podium":
                message = "Podium finish is within reach! 🥇🥈🥉"
            elif bracket == "top_10":
                message = "You could finish in the top 10! 💪"
            else:
                message = "Every competition is a chance to improve! 🎣"

        return PerformancePrediction(
            predicted_bracket=bracket,
            confidence=confidence,
            message=message,
            probabilities=prediction["probabilities"],
            competition=competition_context,
        )
    else:
        # Fallback based on stats
        if user_stats and user_stats.total_wins > 0:
            bracket = "podium"
            message = "Based on your win history, podium is possible!"
        elif user_stats and user_stats.podium_finishes > 0:
            bracket = "top_10"
            message = "Your experience puts you in contention!"
        else:
            bracket = "other"
            message = "Every competition is a chance to improve! 🎣"

        return PerformancePrediction(
            predicted_bracket=bracket,
            confidence=0.5,
            message=message,
            probabilities={"winner": 0.1, "podium": 0.2, "top_10": 0.3, "other": 0.4},
            competition=competition_context,
        )
