"""Pydantic schemas for organizer permission management."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ============================================================================
# Event Type Access Schemas
# ============================================================================


class EventTypeAccessCreate(BaseModel):
    """Schema for granting event type access to an organizer."""

    user_id: int = Field(..., description="User ID to grant access to")
    event_type_id: int = Field(..., description="Event type ID to grant access for")
    notes: Optional[str] = Field(None, max_length=500, description="Optional notes about this grant")


class EventTypeAccessBulkCreate(BaseModel):
    """Schema for granting multiple event types to a user at once."""

    user_id: int = Field(..., description="User ID to grant access to")
    event_type_ids: List[int] = Field(..., min_length=1, description="List of event type IDs to grant")
    notes: Optional[str] = Field(None, max_length=500, description="Optional notes about this grant")


class EventTypeAccessResponse(BaseModel):
    """Schema for event type access response."""

    id: int
    user_id: int
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    event_type_id: int
    event_type_name: Optional[str] = None
    granted_by_id: Optional[int] = None
    granted_by_name: Optional[str] = None
    granted_at: datetime
    notes: Optional[str] = None
    is_active: bool

    class Config:
        from_attributes = True


class EventTypeAccessListResponse(BaseModel):
    """Paginated list response for event type access."""

    items: List[EventTypeAccessResponse]
    total: int


# ============================================================================
# National Event Organizer Schemas
# ============================================================================


class NationalOrganizerCreate(BaseModel):
    """Schema for granting national event permission to an organizer."""

    user_id: int = Field(..., description="User ID to grant national permission to")
    reason: Optional[str] = Field(None, max_length=500, description="Reason for granting permission")


class NationalOrganizerResponse(BaseModel):
    """Schema for national organizer response."""

    id: int
    user_id: int
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    granted_by_id: Optional[int] = None
    granted_by_name: Optional[str] = None
    granted_at: datetime
    reason: Optional[str] = None
    is_active: bool

    class Config:
        from_attributes = True


class NationalOrganizerListResponse(BaseModel):
    """Paginated list response for national organizers."""

    items: List[NationalOrganizerResponse]
    total: int


# ============================================================================
# Summary Response
# ============================================================================


class OrganizerPermissionSummary(BaseModel):
    """Summary of an organizer's permissions."""

    user_id: int
    user_name: str
    user_email: str
    event_type_access: List[EventTypeAccessResponse]
    can_create_national: bool
    national_permission: Optional[NationalOrganizerResponse] = None


# ============================================================================
# User Search for Permission Assignment
# ============================================================================


class OrganizerSearchResult(BaseModel):
    """Search result for organizer users."""

    id: int
    email: str
    first_name: str
    last_name: str
    full_name: str
    avatar_url: Optional[str] = None

    class Config:
        from_attributes = True


class OrganizerSearchResponse(BaseModel):
    """Response for organizer search."""

    items: List[OrganizerSearchResult]
    total: int
