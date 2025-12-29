"""Schemas for admin message contact form."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AdminMessageCreate(BaseModel):
    """Request schema for creating a message to admin."""

    subject: str = Field(..., min_length=5, max_length=200)
    message: str = Field(..., min_length=10, max_length=2000)


class AdminMessageResponse(BaseModel):
    """Response schema for admin message."""

    id: int
    sender_id: int
    sender_name: str
    sender_email: str
    sender_phone: Optional[str] = None
    subject: str
    message: str
    is_read: bool
    read_at: Optional[datetime] = None
    read_by_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AdminMessageListResponse(BaseModel):
    """Paginated list response for admin messages."""

    items: list[AdminMessageResponse]
    total: int
    unread_count: int
    page: int
    page_size: int
    pages: int


class AdminMessageSendResponse(BaseModel):
    """Response after successfully sending a message."""

    success: bool = True
    message: str = "Message sent successfully"


class AdminUnreadCountResponse(BaseModel):
    """Response for unread message count."""

    unread_count: int
