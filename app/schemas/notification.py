"""Notification schemas for request/response validation."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AudienceType(str, Enum):
    """Target audience type for notifications."""

    INDIVIDUAL = "individual"
    EVENT_PARTICIPANTS = "event_participants"
    CLUB_MEMBERS = "club_members"
    ALL_ORGANIZERS = "all_organizers"
    ALL_USERS = "all_users"


class TargetedNotificationRequest(BaseModel):
    """Schema for sending targeted push notifications."""

    audience_type: AudienceType
    audience_id: Optional[int] = None  # event_id, club_id, or user_id
    user_email: Optional[str] = None  # for individual lookup
    title: str = Field(..., min_length=1, max_length=100)
    body: str = Field(..., min_length=1, max_length=500)
    data: Optional[dict] = None


class TargetedNotificationResponse(BaseModel):
    """Response for targeted notification send."""

    success: bool
    message: str
    recipient_count: int
    task_id: Optional[str] = None


class NotificationCreate(BaseModel):
    """Schema for creating a notification (internal use)."""

    user_id: int
    type: str
    title: str
    message: str
    data: Optional[dict] = None


class NotificationResponse(BaseModel):
    """Schema for notification response."""

    id: int
    user_id: int
    type: str
    title: str
    message: str
    data: Optional[dict] = None
    is_read: bool
    read_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    """Paginated notification list response."""

    items: list[NotificationResponse]
    total: int
    unread_count: int
    page: int
    page_size: int
    pages: int


class NotificationStats(BaseModel):
    """Notification statistics."""

    total: int
    unread: int
