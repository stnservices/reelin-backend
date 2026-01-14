"""Admin ML Predictions API endpoints."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.permissions import AdminOnly
from app.models.user import UserAccount
from app.models.event import Event
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.services.ml_service import MLService

router = APIRouter()


class AttendancePredictionResponse(BaseModel):
    """Response for attendance prediction."""
    event_id: int
    event_name: str
    predicted_attendance: int
    current_enrollment: int
    confidence: str
    historical_avg: Optional[float] = None


class EventAnalyticsResponse(BaseModel):
    """Full analytics for an event."""
    event_id: int
    event_name: str
    predicted_attendance: int
    current_enrollment: int
    historical_avg_attendance: Optional[float]
    historical_avg_catches: Optional[float]
    is_weekend: bool
    is_national: bool


@router.get("/events/{event_id}/predicted-attendance", response_model=AttendancePredictionResponse)
async def get_predicted_attendance(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> AttendancePredictionResponse:
    """
    Get predicted attendance for an event.
    Admin only endpoint for event planning.
    """
    # Get event
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get current enrollment count
    enrollment_stmt = select(func.count()).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.status == EnrollmentStatus.APPROVED.value,
    )
    enrollment_result = await db.execute(enrollment_stmt)
    current_enrollment = enrollment_result.scalar() or 0

    # Get historical average for this event type
    hist_stmt = (
        select(func.avg(func.count()))
        .select_from(EventEnrollment)
        .join(Event, Event.id == EventEnrollment.event_id)
        .where(
            Event.event_type_id == event.event_type_id,
            Event.status == "completed",
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
        .group_by(EventEnrollment.event_id)
    )
    # Simpler approach - count enrollments per completed event of same type
    completed_events_stmt = (
        select(Event.id)
        .where(
            Event.event_type_id == event.event_type_id,
            Event.status == "completed",
        )
    )
    completed_result = await db.execute(completed_events_stmt)
    completed_event_ids = [row[0] for row in completed_result.all()]

    historical_avg = None
    if completed_event_ids:
        total_enrollments = 0
        for eid in completed_event_ids:
            count_stmt = select(func.count()).where(
                EventEnrollment.event_id == eid,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
            )
            count_result = await db.execute(count_stmt)
            total_enrollments += count_result.scalar() or 0
        historical_avg = total_enrollments / len(completed_event_ids)

    # Build features for ML prediction
    event_date = event.start_date or datetime.now(timezone.utc)
    features = {
        "event_type_id": event.event_type_id or 1,
        "day_of_week": event_date.weekday(),
        "month": event_date.month,
        "is_weekend": 1 if event_date.weekday() >= 5 else 0,
        "is_national_event": 1 if event.is_national_event else 0,
        "is_team_event": 1 if event.is_team_event else 0,
        "hist_avg_attendance": historical_avg or 0,
        "hist_event_count": len(completed_event_ids),
        "is_summer": 1 if event_date.month in [6, 7, 8] else 0,
        "is_winter": 1 if event_date.month in [12, 1, 2] else 0,
    }

    # Get ML prediction
    ml_service = MLService(db)
    prediction = await ml_service.predict_event_attendance(features)

    if prediction:
        predicted = prediction["predicted_attendance"]
        confidence = prediction["confidence"]
    else:
        # Fallback to historical average
        predicted = int(historical_avg) if historical_avg else current_enrollment
        confidence = "low"

    return AttendancePredictionResponse(
        event_id=event.id,
        event_name=event.name,
        predicted_attendance=predicted,
        current_enrollment=current_enrollment,
        confidence=confidence,
        historical_avg=historical_avg,
    )


@router.get("/events/{event_id}/analytics", response_model=EventAnalyticsResponse)
async def get_event_analytics(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> EventAnalyticsResponse:
    """
    Get full analytics for an event including predictions.
    Admin only endpoint.
    """
    # Get event
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get current enrollment
    enrollment_stmt = select(func.count()).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.status == EnrollmentStatus.APPROVED.value,
    )
    enrollment_result = await db.execute(enrollment_stmt)
    current_enrollment = enrollment_result.scalar() or 0

    # Get historical averages
    completed_stmt = select(Event.id).where(
        Event.event_type_id == event.event_type_id,
        Event.status == "completed",
    )
    completed_result = await db.execute(completed_stmt)
    completed_ids = [r[0] for r in completed_result.all()]

    hist_avg_attendance = None
    hist_avg_catches = None

    if completed_ids:
        # Calculate averages
        total_enrollments = 0
        total_catches = 0

        for eid in completed_ids:
            e_stmt = select(func.count()).where(
                EventEnrollment.event_id == eid,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
            )
            e_result = await db.execute(e_stmt)
            total_enrollments += e_result.scalar() or 0

            from app.models.catch import Catch, CatchStatus
            c_stmt = select(func.count()).where(
                Catch.event_id == eid,
                Catch.status == CatchStatus.APPROVED.value,
            )
            c_result = await db.execute(c_stmt)
            total_catches += c_result.scalar() or 0

        hist_avg_attendance = total_enrollments / len(completed_ids)
        hist_avg_catches = total_catches / len(completed_ids)

    # Get ML prediction
    event_date = event.start_date or datetime.now(timezone.utc)
    features = {
        "event_type_id": event.event_type_id or 1,
        "day_of_week": event_date.weekday(),
        "month": event_date.month,
        "is_weekend": 1 if event_date.weekday() >= 5 else 0,
        "is_national_event": 1 if event.is_national_event else 0,
        "is_team_event": 1 if event.is_team_event else 0,
        "hist_avg_attendance": hist_avg_attendance or 0,
        "hist_event_count": len(completed_ids),
        "is_summer": 1 if event_date.month in [6, 7, 8] else 0,
        "is_winter": 1 if event_date.month in [12, 1, 2] else 0,
    }

    ml_service = MLService(db)
    prediction = await ml_service.predict_event_attendance(features)
    predicted = prediction["predicted_attendance"] if prediction else int(hist_avg_attendance or current_enrollment)

    return EventAnalyticsResponse(
        event_id=event.id,
        event_name=event.name,
        predicted_attendance=predicted,
        current_enrollment=current_enrollment,
        historical_avg_attendance=hist_avg_attendance,
        historical_avg_catches=hist_avg_catches,
        is_weekend=event_date.weekday() >= 5,
        is_national=event.is_national_event or False,
    )
