"""Live tracking and status endpoints.

GPS tracking uses Firebase Realtime Database for real-time position updates.
This module provides REST endpoints for:
- Event status queries
- GPS tracking start/stop/position updates (stored in Redis for quick access)
- Route history persistence (stored in PostgreSQL)
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.event import Event, EventStatus
from app.models.event_validator import EventValidator
from app.models.route_history import RouteHistory
from app.models.user import UserAccount
from app.dependencies import get_current_user, get_current_user_id_cached, get_current_user_optional
from app.services.redis_cache import redis_cache


router = APIRouter()


class EventLiveStatusResponse(BaseModel):
    """Response model for event live status."""
    event_id: int
    slug: str
    name: str
    status: str
    status_message: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_live: bool  # True only if event is ongoing
    event_type: str  # street_fishing, trout_area


@router.get("/events/{event_id}/status", response_model=EventLiveStatusResponse)
async def get_event_live_status(
    event_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get event status for live page display.
    Returns appropriate messaging based on event state.
    This is a PUBLIC endpoint - no authentication required.
    """
    query = select(Event).where(Event.id == event_id)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Determine status message based on event state
    status_messages = {
        EventStatus.DRAFT.value: "Event is being prepared",
        EventStatus.PUBLISHED.value: f"Event starts on {event.start_date.strftime('%B %d, %Y at %H:%M') if event.start_date else 'TBD'}",
        EventStatus.ONGOING.value: "Event is live!",
        EventStatus.COMPLETED.value: "Event has ended. View final results below.",
        EventStatus.CANCELLED.value: "This event has been cancelled",
    }

    return EventLiveStatusResponse(
        event_id=event.id,
        slug=event.slug,
        name=event.name,
        status=event.status,
        status_message=status_messages.get(event.status, "Unknown status"),
        start_date=event.start_date.isoformat() if event.start_date else None,
        end_date=event.end_date.isoformat() if event.end_date else None,
        is_live=event.status == EventStatus.ONGOING.value,
        event_type=event.event_type.code if event.event_type else "street_fishing",
    )


@router.get("/events/by-slug/{slug}/status", response_model=EventLiveStatusResponse)
async def get_event_live_status_by_slug(
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get event status for live page display by slug.
    Returns appropriate messaging based on event state.
    This is a PUBLIC endpoint - no authentication required.
    """
    query = select(Event).where(Event.slug == slug)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Determine status message based on event state
    status_messages = {
        EventStatus.DRAFT.value: "Event is being prepared",
        EventStatus.PUBLISHED.value: f"Event starts on {event.start_date.strftime('%B %d, %Y at %H:%M') if event.start_date else 'TBD'}",
        EventStatus.ONGOING.value: "Event is live!",
        EventStatus.COMPLETED.value: "Event has ended. View final results below.",
        EventStatus.CANCELLED.value: "This event has been cancelled",
    }

    return EventLiveStatusResponse(
        event_id=event.id,
        slug=event.slug,
        name=event.name,
        status=event.status,
        status_message=status_messages.get(event.status, "Unknown status"),
        start_date=event.start_date.isoformat() if event.start_date else None,
        end_date=event.end_date.isoformat() if event.end_date else None,
        is_live=event.status == EventStatus.ONGOING.value,
        event_type=event.event_type.code if event.event_type else "street_fishing",
    )


# === Live GPS Tracking Endpoints ===


class PositionUpdate(BaseModel):
    """Schema for position update from mobile app."""

    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    accuracy: float = Field(0, ge=0)
    speed: float = Field(0, ge=0)  # m/s
    heading: float = Field(0, ge=0, le=360)
    is_inside_geofence: bool = True


async def _get_enrollment(
    db: AsyncSession, event_id: int, user_id: int
) -> Optional[EventEnrollment]:
    """Get user's enrollment for an event."""
    query = select(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.user_id == user_id,
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def _get_event(db: AsyncSession, event_id: int) -> Optional[Event]:
    """Get event by ID."""
    query = select(Event).where(Event.id == event_id, Event.is_deleted == False)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def _get_user_display_info(
    db: AsyncSession, user_id: int
) -> tuple[str, Optional[str]]:
    """Get user's display name and avatar URL."""
    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == user_id)
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if user:
        # Get name from profile if available
        if user.profile:
            name_parts = [user.profile.first_name, user.profile.last_name]
            display_name = " ".join(p for p in name_parts if p) or user.email.split("@")[0]
        else:
            display_name = user.email.split("@")[0]
        # Use effective avatar (profile picture > OAuth avatar)
        avatar_url = user.effective_avatar_url
        return display_name, avatar_url
    return f"User {user_id}", None


async def _can_view_tracking(
    db: AsyncSession, event: Event, user: UserAccount
) -> bool:
    """
    Check if user can view live tracking map.
    Allowed: admins, event organizer, event validators.
    """
    # Admin can view all
    if user.is_superuser or (user.profile and user.profile.is_administrator):
        return True

    # Event organizer can view
    if event.created_by_id == user.id:
        return True

    # Event validators can view
    query = select(EventValidator).where(
        EventValidator.event_id == event.id,
        EventValidator.validator_id == user.id,
        EventValidator.is_active == True,
    )
    result = await db.execute(query)
    if result.scalar_one_or_none():
        return True

    return False


@router.post("/events/{event_id}/tracking/start")
async def start_tracking(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Start tracking session for current user in an event.
    Requires approved enrollment or organizer/validator status.

    Note: Live position broadcasts are handled by Firebase on mobile.
    This endpoint initializes Redis storage for quick position lookups.
    """
    # Check event exists and is ongoing
    event = await _get_event(db, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if event.status != EventStatus.ONGOING.value:
        raise HTTPException(
            status_code=400, detail="Tracking only available for ongoing events"
        )

    # Check if user can track (enrolled and approved, or organizer/validator)
    is_organizer = event.created_by_id == current_user.id
    is_admin = current_user.is_superuser or (current_user.profile and current_user.profile.is_administrator)

    if not is_organizer and not is_admin:
        enrollment = await _get_enrollment(db, event_id, current_user.id)
        if not enrollment or enrollment.status != EnrollmentStatus.APPROVED:
            raise HTTPException(
                status_code=403, detail="Must be enrolled and approved to track"
            )

    # Get display info
    display_name, avatar_url = await _get_user_display_info(db, current_user.id)

    # Initialize position in Redis (0,0 until first real update)
    initial_position = {
        "user_id": current_user.id,
        "display_name": display_name,
        "avatar_url": avatar_url,
        "lat": 0.0,
        "lng": 0.0,
        "accuracy": 0.0,
        "speed": 0.0,
        "heading": 0.0,
        "is_inside_geofence": True,
        "is_online": True,
        "updated_at": datetime.utcnow().isoformat(),
    }

    await redis_cache.update_participant_position(
        event_id, current_user.id, initial_position
    )

    logger.info(f"User {current_user.id} started tracking for event {event_id}")

    return {"status": "tracking_started", "event_id": event_id}


@router.post("/events/{event_id}/tracking/stop")
async def stop_tracking(
    event_id: int,
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Stop tracking session for current user.
    Removes their position from Redis.
    """
    # Remove from Redis
    await redis_cache.remove_participant_position(event_id, user_id)

    logger.info(f"User {user_id} stopped tracking for event {event_id}")

    return {"status": "tracking_stopped", "event_id": event_id}


@router.post("/events/{event_id}/position")
async def update_position(
    event_id: int,
    position: PositionUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Update current user's position in Redis.
    Called periodically by mobile app during tracking.

    Note: Live broadcasts to other clients are handled by Firebase.
    This stores positions for quick lookup by organizers/validators.
    """
    # Check if user has an active tracking session
    existing = await redis_cache.get_participant_position(event_id, user_id)
    if not existing:
        # Auto-start tracking if not started
        event = await _get_event(db, event_id)
        if not event or event.status != EventStatus.ONGOING.value:
            raise HTTPException(status_code=400, detail="Event not trackable")

        display_name, avatar_url = await _get_user_display_info(db, user_id)
    else:
        display_name = existing.get("display_name", f"User {user_id}")
        avatar_url = existing.get("avatar_url")

    # Update position
    position_data = {
        "user_id": user_id,
        "display_name": display_name,
        "avatar_url": avatar_url,
        "lat": position.lat,
        "lng": position.lng,
        "accuracy": position.accuracy,
        "speed": position.speed,
        "heading": position.heading,
        "is_inside_geofence": position.is_inside_geofence,
        "is_online": True,
        "updated_at": datetime.utcnow().isoformat(),
    }

    await redis_cache.update_participant_position(event_id, user_id, position_data)

    return {"status": "position_updated"}


@router.get("/events/{event_id}/participants")
async def get_tracking_participants(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get all participant positions for an event from Redis.
    Restricted to admins, event organizer, and event validators.
    """
    # Check event exists
    event = await _get_event(db, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check if user can view tracking
    if not await _can_view_tracking(db, event, current_user):
        raise HTTPException(
            status_code=403,
            detail="Only admins, organizers, and validators can view live tracking"
        )

    # Get all positions from Redis
    positions = await redis_cache.get_all_participant_positions(event_id)

    # Convert to list
    participants = list(positions.values())

    return {
        "event_id": event_id,
        "participant_count": len(participants),
        "participants": participants,
    }


@router.post("/events/{event_id}/tracking/heartbeat")
async def tracking_heartbeat(
    event_id: int,
    user_id: int = Depends(get_current_user_id_cached),
):
    """
    Send heartbeat to maintain online status in Redis.
    Called periodically even when position hasn't changed.
    """
    existing = await redis_cache.get_participant_position(event_id, user_id)
    if existing:
        existing["is_online"] = True
        existing["updated_at"] = datetime.utcnow().isoformat()
        await redis_cache.update_participant_position(
            event_id, user_id, existing
        )
        return {"status": "heartbeat_received"}

    return {"status": "not_tracking"}


# === Route History Endpoints ===


class RouteHistoryCreate(BaseModel):
    """Schema for creating route history."""

    event_id: int
    user_id: int
    display_name: str
    started_at: datetime
    ended_at: datetime
    total_distance_km: float = 0.0
    average_speed_kmh: float = 0.0
    max_speed_kmh: float = 0.0
    total_time_minutes: int = 0
    geofence_violations: int = 0
    time_outside_geofence_minutes: int = 0
    point_count: int = 0
    points: list = []


@router.post("/events/{event_id}/route-history")
async def save_route_history(
    event_id: int,
    route_data: RouteHistoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Save route history when user stops tracking.
    This stores the compressed route in PostgreSQL for later analysis.
    """
    # Verify user is saving their own route or is admin
    is_admin = current_user.is_superuser or (current_user.profile and current_user.profile.is_administrator)
    if route_data.user_id != current_user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Cannot save route for another user")

    # Check if route history already exists (upsert)
    query = select(RouteHistory).where(
        RouteHistory.event_id == event_id,
        RouteHistory.user_id == route_data.user_id,
    )
    result = await db.execute(query)
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing
        existing.display_name = route_data.display_name
        existing.started_at = route_data.started_at
        existing.ended_at = route_data.ended_at
        existing.total_distance_km = route_data.total_distance_km
        existing.average_speed_kmh = route_data.average_speed_kmh
        existing.max_speed_kmh = route_data.max_speed_kmh
        existing.total_time_minutes = route_data.total_time_minutes
        existing.geofence_violations = route_data.geofence_violations
        existing.time_outside_geofence_minutes = route_data.time_outside_geofence_minutes
        existing.point_count = route_data.point_count
        existing.points = route_data.points
        route_history = existing
    else:
        # Create new
        route_history = RouteHistory(
            event_id=event_id,
            user_id=route_data.user_id,
            display_name=route_data.display_name,
            started_at=route_data.started_at,
            ended_at=route_data.ended_at,
            total_distance_km=route_data.total_distance_km,
            average_speed_kmh=route_data.average_speed_kmh,
            max_speed_kmh=route_data.max_speed_kmh,
            total_time_minutes=route_data.total_time_minutes,
            geofence_violations=route_data.geofence_violations,
            time_outside_geofence_minutes=route_data.time_outside_geofence_minutes,
            point_count=route_data.point_count,
            points=route_data.points,
        )
        db.add(route_history)

    await db.commit()
    await db.refresh(route_history)

    logger.info(
        f"Saved route history for user {route_data.user_id} in event {event_id}: "
        f"{route_data.point_count} points, {route_data.total_distance_km:.2f} km"
    )

    return {
        "status": "route_saved",
        "route_id": route_history.id,
        "point_count": route_history.point_count,
    }


@router.get("/events/{event_id}/route-history/{user_id}")
async def get_route_history(
    event_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[UserAccount] = Depends(get_current_user_optional),
):
    """
    Get route history for a specific user in an event.
    Public endpoint - useful for reviewing participant routes.
    """
    query = select(RouteHistory).where(
        RouteHistory.event_id == event_id,
        RouteHistory.user_id == user_id,
    )
    result = await db.execute(query)
    route = result.scalar_one_or_none()

    if not route:
        raise HTTPException(status_code=404, detail="Route history not found")

    return {
        "event_id": route.event_id,
        "user_id": route.user_id,
        "display_name": route.display_name,
        "started_at": route.started_at.isoformat(),
        "ended_at": route.ended_at.isoformat(),
        "total_distance_km": route.total_distance_km,
        "average_speed_kmh": route.average_speed_kmh,
        "max_speed_kmh": route.max_speed_kmh,
        "total_time_minutes": route.total_time_minutes,
        "geofence_violations": route.geofence_violations,
        "time_outside_geofence_minutes": route.time_outside_geofence_minutes,
        "point_count": route.point_count,
        "points": route.points,
    }


@router.get("/events/{event_id}/route-history")
async def list_route_histories(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[UserAccount] = Depends(get_current_user_optional),
):
    """
    List all route histories for an event.
    Returns summary without full route points.
    """
    query = select(RouteHistory).where(RouteHistory.event_id == event_id)
    result = await db.execute(query)
    routes = result.scalars().all()

    return {
        "event_id": event_id,
        "count": len(routes),
        "routes": [
            {
                "user_id": r.user_id,
                "display_name": r.display_name,
                "started_at": r.started_at.isoformat(),
                "ended_at": r.ended_at.isoformat(),
                "total_distance_km": r.total_distance_km,
                "total_time_minutes": r.total_time_minutes,
                "point_count": r.point_count,
            }
            for r in routes
        ],
    }
