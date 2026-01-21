"""Schemas for event chat messaging."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ChatMessageCreate(BaseModel):
    """Request schema for creating a chat message."""

    message: str = Field(..., min_length=1, max_length=2000)
    message_type: str = Field(
        default="message",
        pattern="^(message|announcement)$",
        description="Type of message: 'message' for regular, 'announcement' for highlighted"
    )


class ChatMessageResponse(BaseModel):
    """Response schema for a chat message."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    user_id: int
    user_name: str
    user_avatar: Optional[str] = None
    is_organizer: bool
    message: str
    message_type: str
    is_pinned: bool
    pinned_at: Optional[datetime] = None
    created_at: datetime


class ChatMessageListResponse(BaseModel):
    """Paginated list response for chat messages."""

    items: list[ChatMessageResponse]
    total: int
    has_more: bool
    oldest_id: Optional[int] = None  # For cursor-based pagination


class ChatMessageSendResponse(BaseModel):
    """Response after successfully sending a message."""

    success: bool = True
    message: ChatMessageResponse


class ChatMessageDeleteResponse(BaseModel):
    """Response after deleting a message."""

    success: bool = True
    message_id: int


class ChatMessagePinResponse(BaseModel):
    """Response after pinning/unpinning a message."""

    success: bool = True
    message_id: int
    is_pinned: bool


class ChatEventPayload(BaseModel):
    """Payload for real-time chat events via Redis/SSE."""

    type: str  # new_message, message_deleted, message_pinned, message_unpinned
    event_id: int
    message: Optional[ChatMessageResponse] = None
    message_id: Optional[int] = None
    is_pinned: Optional[bool] = None


class ChatUnreadCountResponse(BaseModel):
    """Response for unread message count (for future use)."""

    event_id: int
    unread_count: int
    last_read_at: Optional[datetime] = None
