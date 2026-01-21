"""Event chat endpoints for real-time messaging within events."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount
from app.models.event import Event, EventStatus
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.event_chat import EventChatMessage, MessageType
from app.models.event_validator import EventValidator
from app.schemas.event_chat import (
    ChatMessageCreate,
    ChatMessageResponse,
    ChatMessageListResponse,
    ChatMessageSendResponse,
    ChatMessageDeleteResponse,
    ChatMessagePinResponse,
)
from app.services.redis_cache import redis_cache

router = APIRouter()
logger = logging.getLogger(__name__)


async def get_enrollment(db: AsyncSession, event_id: int, user_id: int) -> Optional[EventEnrollment]:
    """Get user's enrollment for an event."""
    query = select(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.user_id == user_id,
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def is_user_approved(db: AsyncSession, event_id: int, user_id: int) -> bool:
    """Check if user has approved enrollment in the event."""
    enrollment = await get_enrollment(db, event_id, user_id)
    return enrollment is not None and enrollment.status == EnrollmentStatus.APPROVED.value


async def is_event_organizer(db: AsyncSession, event_id: int, user_id: int) -> bool:
    """Check if user is the event organizer."""
    query = select(Event).where(Event.id == event_id)
    result = await db.execute(query)
    event = result.scalar_one_or_none()
    return event is not None and event.created_by_id == user_id


async def is_event_validator(db: AsyncSession, event_id: int, user_id: int) -> bool:
    """Check if user is an active validator for the event."""
    query = select(EventValidator).where(
        EventValidator.event_id == event_id,
        EventValidator.validator_id == user_id,
        EventValidator.is_active == True,
    )
    result = await db.execute(query)
    return result.scalar_one_or_none() is not None


async def can_access_chat(db: AsyncSession, event_id: int, user_id: int) -> tuple[bool, bool]:
    """
    Check if user can access the chat.
    Returns (can_access, is_organizer_or_validator)

    Access granted to:
    - Event organizer (creator)
    - Active validators (judges)
    - Approved enrolled participants
    """
    # Check if organizer
    is_organizer = await is_event_organizer(db, event_id, user_id)
    if is_organizer:
        return True, True

    # Check if validator (judges can also manage chat like organizers)
    is_validator = await is_event_validator(db, event_id, user_id)
    if is_validator:
        return True, True

    # Check if approved enrollment
    is_approved = await is_user_approved(db, event_id, user_id)
    return is_approved, False


def message_to_response(msg: EventChatMessage, is_organizer: bool) -> ChatMessageResponse:
    """Convert EventChatMessage model to response schema."""
    user_profile = msg.user.profile if msg.user and msg.user.profile else None
    user_name = user_profile.full_name if user_profile else f"User {msg.user_id}"
    user_avatar = user_profile.profile_picture_url if user_profile else None

    return ChatMessageResponse(
        id=msg.id,
        event_id=msg.event_id,
        user_id=msg.user_id,
        user_name=user_name,
        user_avatar=user_avatar,
        is_organizer=is_organizer,
        message=msg.message,
        message_type=msg.message_type,
        is_pinned=msg.is_pinned,
        pinned_at=msg.pinned_at,
        created_at=msg.created_at,
    )




@router.get("/events/{event_id}/chat", response_model=ChatMessageListResponse)
async def get_chat_messages(
    event_id: int,
    before: Optional[int] = Query(None, description="Get messages before this ID (for pagination)"),
    limit: int = Query(50, ge=1, le=100, description="Number of messages to return"),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get chat messages for an event.

    - Only enrolled participants (approved) and organizers can access
    - Returns newest messages first
    - Use 'before' parameter for pagination (load older messages)
    - Uses Redis cache for faster reads
    """
    # Check access
    can_access, _ = await can_access_chat(db, event_id, current_user.id)
    if not can_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be enrolled (approved) to access event chat"
        )

    # Check event exists and is not deleted
    event_query = select(Event).where(Event.id == event_id, Event.is_deleted == False)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    organizer_id = event.created_by_id

    # Try Redis cache first (only for initial load without pagination)
    if before is None:
        cached_messages = await redis_cache.get_cached_chat_messages(event_id, limit)
        if cached_messages:
            logger.debug(f"Chat cache hit for event {event_id}: {len(cached_messages)} messages")
            return ChatMessageListResponse(
                items=[ChatMessageResponse(**m) for m in cached_messages],
                total=len(cached_messages),
                has_more=len(cached_messages) >= limit,
                oldest_id=cached_messages[0]["id"] if cached_messages else None,
            )

    # Cache miss or pagination - fetch from database
    query = (
        select(EventChatMessage)
        .options(
            selectinload(EventChatMessage.user).selectinload(UserAccount.profile)
        )
        .where(
            EventChatMessage.event_id == event_id,
            EventChatMessage.is_deleted == False,
        )
    )

    # Cursor-based pagination
    if before is not None:
        query = query.where(EventChatMessage.id < before)

    # Order by newest first, limit + 1 to check if there are more
    query = query.order_by(EventChatMessage.created_at.desc()).limit(limit + 1)

    result = await db.execute(query)
    messages = list(result.scalars().all())

    # Check if there are more messages
    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

    # Build response items (reverse to show oldest first in the list)
    items = [
        message_to_response(msg, msg.user_id == organizer_id)
        for msg in reversed(messages)
    ]

    # Get total count
    count_query = select(func.count(EventChatMessage.id)).where(
        EventChatMessage.event_id == event_id,
        EventChatMessage.is_deleted == False,
    )
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    oldest_id = messages[-1].id if messages else None

    # Warm cache if this was initial load (no pagination)
    if before is None and items:
        await redis_cache.warm_chat_cache(
            event_id,
            [item.model_dump(mode="json") for item in items]
        )
        logger.debug(f"Warmed chat cache for event {event_id}: {len(items)} messages")

    return ChatMessageListResponse(
        items=items,
        total=total,
        has_more=has_more,
        oldest_id=oldest_id,
    )


@router.get("/events/{event_id}/chat/pinned", response_model=list[ChatMessageResponse])
async def get_pinned_messages(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """Get all pinned messages for an event."""
    # Check access
    can_access, _ = await can_access_chat(db, event_id, current_user.id)
    if not can_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be enrolled (approved) to access event chat"
        )

    # Get event organizer
    event_query = select(Event).where(Event.id == event_id, Event.is_deleted == False)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get pinned messages
    query = (
        select(EventChatMessage)
        .options(
            selectinload(EventChatMessage.user).selectinload(UserAccount.profile)
        )
        .where(
            EventChatMessage.event_id == event_id,
            EventChatMessage.is_deleted == False,
            EventChatMessage.is_pinned == True,
        )
        .order_by(EventChatMessage.pinned_at.desc())
    )

    result = await db.execute(query)
    messages = result.scalars().all()

    organizer_id = event.created_by_id

    return [
        message_to_response(msg, msg.user_id == organizer_id)
        for msg in messages
    ]


@router.post(
    "/events/{event_id}/chat",
    response_model=ChatMessageSendResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_chat_message(
    event_id: int,
    message_data: ChatMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Send a chat message to an event.

    - Only enrolled participants (approved) and organizers can send messages
    - Organizers can send announcements (message_type='announcement')
    - Regular users can only send regular messages
    """
    # Check access
    can_access, is_organizer = await can_access_chat(db, event_id, current_user.id)
    if not can_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be enrolled (approved) to send messages"
        )

    # Check event exists and is accessible
    event_query = select(Event).where(Event.id == event_id, Event.is_deleted == False)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check event status - only allow chat for non-completed events
    # (allow for published, ongoing - not draft or cancelled)
    if event.status in [EventStatus.DRAFT.value, EventStatus.CANCELLED.value]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chat is not available for draft or cancelled events"
        )

    # Only organizers can send announcements
    message_type = message_data.message_type
    if message_type == MessageType.ANNOUNCEMENT and not is_organizer:
        message_type = MessageType.MESSAGE  # Downgrade to regular message

    # Create message
    chat_message = EventChatMessage(
        event_id=event_id,
        user_id=current_user.id,
        message=message_data.message,
        message_type=message_type,
    )

    db.add(chat_message)
    await db.commit()
    await db.refresh(chat_message, ["user"])

    # Load user profile for response
    user_query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == current_user.id)
    )
    user_result = await db.execute(user_query)
    user_with_profile = user_result.scalar_one()
    chat_message.user = user_with_profile

    response_msg = message_to_response(chat_message, is_organizer)

    # Cache message in Redis and publish to Pub/Sub (for SSE subscribers)
    await redis_cache.cache_chat_message(
        event_id,
        response_msg.model_dump(mode="json")
    )

    # TODO: Send push notifications to offline users via FCM

    return ChatMessageSendResponse(success=True, message=response_msg)


@router.delete(
    "/events/{event_id}/chat/{message_id}",
    response_model=ChatMessageDeleteResponse,
)
async def delete_chat_message(
    event_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Delete a chat message.

    - Users can delete their own messages
    - Organizers can delete any message
    """
    # Check access
    can_access, is_organizer = await can_access_chat(db, event_id, current_user.id)
    if not can_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be enrolled (approved) to access chat"
        )

    # Get the message
    query = select(EventChatMessage).where(
        EventChatMessage.id == message_id,
        EventChatMessage.event_id == event_id,
        EventChatMessage.is_deleted == False,
    )
    result = await db.execute(query)
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Check permission - user can delete own messages, organizer can delete any
    if message.user_id != current_user.id and not is_organizer:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own messages"
        )

    # Soft delete
    message.is_deleted = True
    message.deleted_by_id = current_user.id
    message.deleted_at = datetime.now(timezone.utc)

    await db.commit()

    # Remove from Redis cache and broadcast deletion via Pub/Sub
    await redis_cache.delete_cached_chat_message(event_id, message_id)

    return ChatMessageDeleteResponse(success=True, message_id=message_id)


@router.post(
    "/events/{event_id}/chat/{message_id}/pin",
    response_model=ChatMessagePinResponse,
)
async def pin_chat_message(
    event_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Pin a chat message.

    - Only organizers can pin messages
    """
    # Check access - must be organizer
    can_access, is_organizer = await can_access_chat(db, event_id, current_user.id)
    if not can_access or not is_organizer:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organizers can pin messages"
        )

    # Get the message
    query = select(EventChatMessage).where(
        EventChatMessage.id == message_id,
        EventChatMessage.event_id == event_id,
        EventChatMessage.is_deleted == False,
    )
    result = await db.execute(query)
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Pin the message
    message.is_pinned = True
    message.pinned_by_id = current_user.id
    message.pinned_at = datetime.now(timezone.utc)

    await db.commit()

    # Update Redis cache and broadcast pin via Pub/Sub
    await redis_cache.update_cached_chat_message(
        event_id,
        message_id,
        {"is_pinned": True, "pinned_at": message.pinned_at.isoformat()}
    )

    return ChatMessagePinResponse(success=True, message_id=message_id, is_pinned=True)


@router.delete(
    "/events/{event_id}/chat/{message_id}/pin",
    response_model=ChatMessagePinResponse,
)
async def unpin_chat_message(
    event_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Unpin a chat message.

    - Only organizers can unpin messages
    """
    # Check access - must be organizer
    can_access, is_organizer = await can_access_chat(db, event_id, current_user.id)
    if not can_access or not is_organizer:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organizers can unpin messages"
        )

    # Get the message
    query = select(EventChatMessage).where(
        EventChatMessage.id == message_id,
        EventChatMessage.event_id == event_id,
        EventChatMessage.is_deleted == False,
    )
    result = await db.execute(query)
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Unpin the message
    message.is_pinned = False
    message.pinned_by_id = None
    message.pinned_at = None

    await db.commit()

    # Update Redis cache and broadcast unpin via Pub/Sub
    await redis_cache.update_cached_chat_message(
        event_id,
        message_id,
        {"is_pinned": False, "pinned_at": None}
    )

    return ChatMessagePinResponse(success=True, message_id=message_id, is_pinned=False)


@router.get("/events/{event_id}/chat/stream")
async def chat_stream(
    event_id: int,
    token: str = Query(..., description="Auth token for SSE (required since EventSource can't send headers)"),
    db: AsyncSession = Depends(get_db),
):
    """
    SSE endpoint for real-time chat updates.

    Connect to this endpoint to receive real-time chat events:
    - new_message: New message posted
    - message_deleted: Message was deleted
    - message_pinned: Message was pinned
    - message_unpinned: Message was unpinned

    Note: Token is passed as query param since EventSource API doesn't support custom headers.
    """
    from app.core.security import decode_token
    from sqlalchemy.orm import selectinload

    # Validate token from query parameter
    try:
        payload = decode_token(token)
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(status_code=401, detail="Invalid token")
        user_id = int(user_id_str)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Get user from DB
    query = select(UserAccount).options(selectinload(UserAccount.profile)).where(UserAccount.id == user_id)
    result = await db.execute(query)
    current_user = result.scalar_one_or_none()

    if not current_user or not current_user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # Check access
    can_access, _ = await can_access_chat(db, event_id, current_user.id)
    if not can_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be enrolled (approved) to access chat stream"
        )

    # Store current user ID to filter out own messages
    subscriber_user_id = current_user.id

    async def event_generator():
        pubsub = None
        try:
            # Subscribe to Redis Pub/Sub channel for this event
            pubsub = await redis_cache.subscribe_chat_channel(event_id)

            # Send initial connected event
            yield f"event: connected\ndata: {json.dumps({'event_id': event_id, 'user_id': subscriber_user_id})}\n\n"

            while True:
                try:
                    # Wait for messages from Redis Pub/Sub with timeout
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                        timeout=30.0
                    )

                    if message and message["type"] == "message":
                        data = json.loads(message["data"])

                        # Skip new_message events for the sender (prevents duplicates)
                        if data.get("type") == "new_message":
                            msg = data.get("message", {})
                            if msg.get("user_id") == subscriber_user_id:
                                logger.debug(f"Skipping own message for user {subscriber_user_id}")
                                continue

                        yield f"event: chat\ndata: {json.dumps(data)}\n\n"

                except asyncio.TimeoutError:
                    # Send keepalive ping
                    yield f"event: ping\ndata: {{}}\n\n"

        except asyncio.CancelledError:
            logger.debug(f"SSE connection cancelled for event {event_id}")
        except Exception as e:
            logger.error(f"Error in chat stream: {e}")
        finally:
            # Clean up Redis subscription
            if pubsub:
                try:
                    await pubsub.unsubscribe()
                    await pubsub.close()
                except Exception as e:
                    logger.debug(f"Error closing pubsub: {e}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
