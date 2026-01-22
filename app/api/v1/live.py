"""Live scoring and tracking endpoints using Server-Sent Events (SSE)."""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import get_db
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.event import Event, EventStatus
from app.models.route_history import RouteHistory
from app.models.user import UserAccount
from app.dependencies import get_current_user, get_current_user_optional
from app.services.redis_cache import redis_cache


router = APIRouter()

# Background task handle for Redis Pub/Sub listener
_redis_listener_task: Optional[asyncio.Task] = None


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


class LiveScoringService:
    """Service for broadcasting live scoring updates via SSE."""

    _instance = None
    _subscribers: dict[int, list[asyncio.Queue]] = defaultdict(list)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def subscribe(self, event_id: int) -> AsyncGenerator[dict, None]:
        """Subscribe to live updates for an event."""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[event_id].append(queue)
        print(f"SSE: New subscriber for event {event_id}. Total: {len(self._subscribers[event_id])}", flush=True)
        try:
            while True:
                data = await queue.get()
                logger.debug(f"SSE: Sending update to subscriber for event {event_id}: {data.get('type')}")
                yield data
        finally:
            self._subscribers[event_id].remove(queue)
            if not self._subscribers[event_id]:
                del self._subscribers[event_id]
            logger.info(f"SSE: Subscriber disconnected from event {event_id}")

    async def broadcast(self, event_id: int, data: dict) -> None:
        """Broadcast an update to all subscribers of an event."""
        subscriber_count = len(self._subscribers.get(event_id, []))
        print(f"SSE: Broadcasting to {subscriber_count} subscribers for event {event_id}: {data.get('type')}", flush=True)
        for queue in self._subscribers[event_id]:
            await queue.put(data)

    def get_subscriber_count(self, event_id: int) -> int:
        """Get the number of active subscribers for an event."""
        return len(self._subscribers.get(event_id, []))


# Global instance
live_scoring_service = LiveScoringService()


async def _redis_pubsub_listener():
    """
    Background task that listens to Redis Pub/Sub for SSE broadcasts from Celery.

    This bridges the gap between Celery workers (separate processes) and FastAPI's
    SSE subscribers. Celery publishes to Redis, this listener picks it up and
    broadcasts to actual SSE clients.
    """
    logger.info("Starting Redis Pub/Sub listener for SSE bridge")

    retry_count = 0
    max_retry_delay = 60  # Max 60 seconds between retries
    keepalive_interval = 120  # Send ping every 2 minutes to prevent idle timeout
    pubsub = None

    while True:
        try:
            # Cleanup any existing pubsub connection
            if pubsub:
                try:
                    await pubsub.unsubscribe()
                    await pubsub.close()
                except Exception:
                    pass

            pubsub = await redis_cache.get_pubsub()
            # Subscribe to all SSE broadcast channels using pattern
            await pubsub.psubscribe(f"{redis_cache.SSE_CHANNEL_PREFIX}:event_*")

            # Reset retry count on successful connection
            retry_count = 0
            logger.info("Redis Pub/Sub connected and subscribed")

            # Use get_message with timeout for keepalive instead of async for
            while True:
                try:
                    # Wait for message with timeout for keepalive
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=keepalive_interval),
                        timeout=keepalive_interval + 5
                    )

                    if message is None:
                        # No message received, send ping to keep connection alive
                        await pubsub.ping()
                        continue

                    if message["type"] == "pmessage":
                        # Extract event_id from channel name: sse_broadcast:event_5 -> 5
                        channel = message["channel"]
                        event_id_str = channel.split("event_")[-1]
                        event_id = int(event_id_str)

                        # Parse the message data
                        data = json.loads(message["data"])

                        # Broadcast to SSE subscribers
                        subscriber_count = live_scoring_service.get_subscriber_count(event_id)
                        if subscriber_count > 0:
                            logger.info(
                                f"Redis->SSE bridge: Broadcasting {data.get('type')} "
                                f"to {subscriber_count} subscribers for event {event_id}"
                            )
                            await live_scoring_service.broadcast(event_id, data)
                        else:
                            logger.debug(
                                f"Redis->SSE bridge: No subscribers for event {event_id}, "
                                f"skipping {data.get('type')}"
                            )
                except (ValueError, json.JSONDecodeError, KeyError) as e:
                    logger.error(f"Error processing Redis Pub/Sub message: {e}")
                except asyncio.TimeoutError:
                    # Timeout waiting for message, send ping to keep connection alive
                    await pubsub.ping()

        except asyncio.CancelledError:
            logger.info("Redis Pub/Sub listener cancelled")
            # Cleanup before exiting
            if pubsub:
                try:
                    await pubsub.unsubscribe()
                    await pubsub.close()
                except Exception:
                    pass
            raise

        except Exception as e:
            retry_count += 1
            # Exponential backoff: 2^retry_count seconds, capped at max_retry_delay
            retry_delay = min(2 ** retry_count, max_retry_delay)
            logger.error(
                f"Redis Pub/Sub listener error: {e}. "
                f"Reconnecting in {retry_delay}s (attempt {retry_count})"
            )
            await asyncio.sleep(retry_delay)


async def start_redis_listener():
    """Start the Redis Pub/Sub listener as a background task."""
    global _redis_listener_task
    if _redis_listener_task is None or _redis_listener_task.done():
        _redis_listener_task = asyncio.create_task(_redis_pubsub_listener())
        logger.info("Redis Pub/Sub listener task created")


async def stop_redis_listener():
    """Stop the Redis Pub/Sub listener."""
    global _redis_listener_task
    if _redis_listener_task and not _redis_listener_task.done():
        _redis_listener_task.cancel()
        try:
            await _redis_listener_task
        except asyncio.CancelledError:
            pass
        logger.info("Redis Pub/Sub listener stopped")


@router.get("/events/{event_id}")
async def live_scoring_stream(
    event_id: int,
    request: Request,
):
    """
    Server-Sent Events endpoint for live scoring updates.

    This is a PUBLIC endpoint - no authentication required.
    Spectators can view live scoring without logging in.

    Event types:
    - score_update: New catch validated, scores updated
    - ranking_change: Ranking positions changed
    - event_status: Event started/ended/paused

    Example client code:
    ```javascript
    const eventSource = new EventSource('/api/v1/live/events/123');
    eventSource.addEventListener('score_update', (e) => {
        const data = JSON.parse(e.data);
        console.log('Score update:', data);
    });
    ```
    """

    async def event_generator():
        # Increment viewer count in Redis
        try:
            await redis_cache.increment_viewers(event_id)
            viewer_count = await redis_cache.get_viewer_count(event_id)
        except Exception:
            viewer_count = live_scoring_service.get_subscriber_count(event_id) + 1

        # Send initial connection message with viewer count
        # Explicitly JSON serialize to ensure proper formatting (not Python repr with single quotes)
        yield {
            "event": "connected",
            "data": json.dumps({
                "event_id": event_id,
                "message": "Connected to live scoring stream",
                "viewer_count": viewer_count,
            }),
        }

        try:
            # Subscribe to updates
            async for update in live_scoring_service.subscribe(event_id):
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                # Add current viewer count to updates
                try:
                    update["viewer_count"] = await redis_cache.get_viewer_count(event_id)
                except Exception:
                    update["viewer_count"] = live_scoring_service.get_subscriber_count(event_id)

                # Explicitly JSON serialize to ensure proper formatting
                yield {
                    "event": update.get("type", "score_update"),
                    "data": json.dumps(update, default=str),
                }
        finally:
            # Decrement viewer count when client disconnects
            try:
                await redis_cache.decrement_viewers(event_id)
            except Exception:
                pass

    return EventSourceResponse(event_generator())


@router.get("/events/{event_id}/stream-status")
async def get_live_stream_status(event_id: int):
    """Get current live streaming status for an event (viewer count)."""
    return {
        "event_id": event_id,
        "active_viewers": live_scoring_service.get_subscriber_count(event_id),
        "streaming": live_scoring_service.get_subscriber_count(event_id) > 0,
    }


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
    query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if user:
        name_parts = [user.first_name, user.last_name]
        display_name = " ".join(p for p in name_parts if p) or user.email.split("@")[0]
        return display_name, user.avatar_url
    return f"User {user_id}", None


@router.post("/events/{event_id}/tracking/start")
async def start_tracking(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Start tracking session for current user in an event.
    Requires approved enrollment or organizer/validator status.
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

    # Publish join event
    await redis_cache.publish_tracking_event(
        event_id,
        {
            "type": "participant_joined",
            "user_id": current_user.id,
            "display_name": display_name,
            "avatar_url": avatar_url,
        },
    )

    logger.info(f"User {current_user.id} started tracking for event {event_id}")

    return {"status": "tracking_started", "event_id": event_id}


@router.post("/events/{event_id}/tracking/stop")
async def stop_tracking(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Stop tracking session for current user.
    Removes their position from the live map.
    """
    # Remove from Redis
    await redis_cache.remove_participant_position(event_id, current_user.id)

    logger.info(f"User {current_user.id} stopped tracking for event {event_id}")

    return {"status": "tracking_stopped", "event_id": event_id}


@router.post("/events/{event_id}/position")
async def update_position(
    event_id: int,
    position: PositionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Update current user's position.
    Called periodically by mobile app during tracking.
    """
    # Check if user has an active tracking session
    existing = await redis_cache.get_participant_position(event_id, current_user.id)
    if not existing:
        # Auto-start tracking if not started
        event = await _get_event(db, event_id)
        if not event or event.status != EventStatus.ONGOING.value:
            raise HTTPException(status_code=400, detail="Event not trackable")

        display_name, avatar_url = await _get_user_display_info(db, current_user.id)
    else:
        display_name = existing.get("display_name", f"User {current_user.id}")
        avatar_url = existing.get("avatar_url")

    # Update position
    position_data = {
        "user_id": current_user.id,
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

    await redis_cache.update_participant_position(event_id, current_user.id, position_data)

    return {"status": "position_updated"}


@router.get("/events/{event_id}/participants")
async def get_tracking_participants(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[UserAccount] = Depends(get_current_user_optional),
):
    """
    Get all participant positions for an event.
    Public endpoint - anyone can view live tracking.
    """
    # Check event exists
    event = await _get_event(db, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get all positions from Redis
    positions = await redis_cache.get_all_participant_positions(event_id)

    # Convert to list
    participants = list(positions.values())

    return {
        "event_id": event_id,
        "participant_count": len(participants),
        "participants": participants,
    }


@router.get("/events/{event_id}/tracking/stream")
async def stream_tracking_participants(
    event_id: int,
    token: str = Query(None, description="Auth token for SSE"),
    db: AsyncSession = Depends(get_db),
):
    """
    SSE stream for real-time participant position updates.
    Public endpoint - anyone can watch live tracking.
    """
    # Check event exists
    event = await _get_event(db, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    async def event_generator():
        """Generate SSE events from Redis Pub/Sub."""
        pubsub = None
        try:
            # Send initial connection event
            yield f"event: connected\ndata: {{}}\n\n"

            # Send current participants snapshot
            positions = await redis_cache.get_all_participant_positions(event_id)
            yield f"event: snapshot\ndata: {json.dumps(list(positions.values()))}\n\n"

            # Subscribe to updates
            pubsub = await redis_cache.subscribe_tracking_channel(event_id)

            # Stream updates
            while True:
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=30.0,
                    )
                    if message and message["type"] == "message":
                        yield f"event: message\ndata: {message['data']}\n\n"
                    else:
                        # Send keepalive
                        yield f"event: ping\ndata: {{}}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive on timeout
                    yield f"event: ping\ndata: {{}}\n\n"
                except asyncio.CancelledError:
                    break

        except Exception as e:
            logger.error(f"SSE tracking stream error for event {event_id}: {e}")
            yield f"event: error\ndata: {{\"error\": \"Stream error\"}}\n\n"
        finally:
            if pubsub:
                await pubsub.unsubscribe()
                await pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/events/{event_id}/tracking/heartbeat")
async def tracking_heartbeat(
    event_id: int,
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Send heartbeat to maintain online status.
    Called periodically even when position hasn't changed.
    """
    existing = await redis_cache.get_participant_position(event_id, current_user.id)
    if existing:
        existing["is_online"] = True
        existing["updated_at"] = datetime.utcnow().isoformat()
        await redis_cache.update_participant_position(
            event_id, current_user.id, existing
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
