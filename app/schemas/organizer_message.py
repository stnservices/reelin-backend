"""Schemas for organizer message contact form."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class OrganizerMessageCreate(BaseModel):
    """Request schema for creating a message to organizer."""

    subject: str = Field(..., min_length=5, max_length=200)
    message: str = Field(..., min_length=10, max_length=2000)


class OrganizerMessageResponse(BaseModel):
    """Response schema for organizer message."""

    id: int
    event_id: int
    event_name: str
    sender_id: int
    sender_name: str
    sender_email: str
    sender_phone: Optional[str] = None
    is_enrolled: bool
    subject: str
    message: str
    is_read: bool
    read_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class OrganizerMessageListResponse(BaseModel):
    """Paginated list response for organizer messages."""

    items: list[OrganizerMessageResponse]
    total: int
    page: int
    per_page: int
    pages: int


class OrganizerMessageSendResponse(BaseModel):
    """Response after successfully sending a message."""

    success: bool = True
    message: str = "Message sent successfully"


class UnreadCountResponse(BaseModel):
    """Response for unread message count."""

    unread_count: int
