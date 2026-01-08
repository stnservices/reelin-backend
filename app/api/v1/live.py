"""Live scoring endpoints using Server-Sent Events (SSE)."""

import asyncio
import json
import logging
from collections import defaultdict
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Request, Depends, HTTPException

logger = logging.getLogger(__name__)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import get_db
from app.models.event import Event, EventStatus
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

    try:
        pubsub = await redis_cache.get_pubsub()
        # Subscribe to all SSE broadcast channels using pattern
        await pubsub.psubscribe(f"{redis_cache.SSE_CHANNEL_PREFIX}:event_*")

        async for message in pubsub.listen():
            if message["type"] == "pmessage":
                try:
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

    except asyncio.CancelledError:
        logger.info("Redis Pub/Sub listener cancelled")
        raise
    except Exception as e:
        logger.error(f"Redis Pub/Sub listener error: {e}")
        # Try to reconnect after a delay
        await asyncio.sleep(5)
        asyncio.create_task(_redis_pubsub_listener())


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
