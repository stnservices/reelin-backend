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
from app.schemas.event_chat import (
    ChatMessageCreate,
    ChatMessageResponse,
    ChatMessageListResponse,
    ChatMessageSendResponse,
    ChatMessageDeleteResponse,
    ChatMessagePinResponse,
    ChatEventPayload,
)
from app.services.redis_cache import redis_cache
from app.services.firebase_chat_service import (
    sync_chat_message,
    delete_chat_message as firebase_delete_message,
    update_message_pinned,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Chat-specific SSE subscribers
_chat_subscribers: dict[int, list[asyncio.Queue]] = {}


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


async def can_access_chat(db: AsyncSession, event_id: int, user_id: int) -> tuple[bool, bool]:
    """
    Check if user can access the chat.
    Returns (can_access, is_organizer)
    """
    # Check if organizer
    is_organizer = await is_event_organizer(db, event_id, user_id)
    if is_organizer:
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


async def broadcast_chat_event(event_id: int, payload: ChatEventPayload):
    """Broadcast chat event to Redis for SSE delivery."""
    try:
        await redis_cache.publish_chat_message(event_id, payload.model_dump(mode="json"))
    except Exception as e:
        logger.error(f"Failed to broadcast chat event: {e}")


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

    # Build query for messages
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

    # Get organizer info for each message author
    organizer_id = event.created_by_id

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

    # Broadcast to subscribers via Redis
    await broadcast_chat_event(event_id, ChatEventPayload(
        type="new_message",
        event_id=event_id,
        message=response_msg,
    ))

    # Sync to Firebase for mobile clients
    sync_chat_message(
        event_id=event_id,
        message_id=chat_message.id,
        user_id=current_user.id,
        user_name=response_msg.user_name,
        user_avatar=response_msg.user_avatar,
        is_organizer=is_organizer,
        message=message_data.message,
        message_type=message_type,
        is_pinned=False,
        created_at=chat_message.created_at,
    )

    # TODO: Send push notifications to offline users

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

    # Broadcast deletion
    await broadcast_chat_event(event_id, ChatEventPayload(
        type="message_deleted",
        event_id=event_id,
        message_id=message_id,
    ))

    # Sync deletion to Firebase
    firebase_delete_message(event_id, message_id)

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

    # Broadcast pin
    await broadcast_chat_event(event_id, ChatEventPayload(
        type="message_pinned",
        event_id=event_id,
        message_id=message_id,
        is_pinned=True,
    ))

    # Sync to Firebase
    update_message_pinned(event_id, message_id, True, message.pinned_at)

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

    # Broadcast unpin
    await broadcast_chat_event(event_id, ChatEventPayload(
        type="message_unpinned",
        event_id=event_id,
        message_id=message_id,
        is_pinned=False,
    ))

    # Sync to Firebase
    update_message_pinned(event_id, message_id, False, None)

    return ChatMessagePinResponse(success=True, message_id=message_id, is_pinned=False)


@router.get("/events/{event_id}/chat/stream")
async def chat_stream(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    SSE endpoint for real-time chat updates.

    Connect to this endpoint to receive real-time chat events:
    - new_message: New message posted
    - message_deleted: Message was deleted
    - message_pinned: Message was pinned
    - message_unpinned: Message was unpinned
    """
    # Check access
    can_access, _ = await can_access_chat(db, event_id, current_user.id)
    if not can_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be enrolled (approved) to access chat stream"
        )

    # Create queue for this subscriber
    queue: asyncio.Queue = asyncio.Queue()

    # Register subscriber
    if event_id not in _chat_subscribers:
        _chat_subscribers[event_id] = []
    _chat_subscribers[event_id].append(queue)

    async def event_generator():
        try:
            # Send initial connected event
            yield f"event: connected\ndata: {json.dumps({'event_id': event_id})}\n\n"

            while True:
                try:
                    # Wait for messages with timeout for keepalive
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"event: chat\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f"event: ping\ndata: {{}}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            # Unregister subscriber
            if event_id in _chat_subscribers:
                try:
                    _chat_subscribers[event_id].remove(queue)
                    if not _chat_subscribers[event_id]:
                        del _chat_subscribers[event_id]
                except ValueError:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Function to broadcast to local SSE subscribers (called from Redis listener)
async def broadcast_to_chat_subscribers(event_id: int, data: dict):
    """Broadcast data to all SSE subscribers for an event."""
    if event_id in _chat_subscribers:
        for queue in _chat_subscribers[event_id]:
            try:
                await queue.put(data)
            except Exception as e:
                logger.error(f"Failed to put message in queue: {e}")
