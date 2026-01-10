"""Team-related Pydantic schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class TeamMemberCreate(BaseModel):
    """Schema for adding a member to a team."""
    enrollment_id: int
    role: Optional[str] = Field("member", pattern="^(captain|member)$")


class TeamMemberResponse(BaseModel):
    """Schema for team member response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    team_id: int
    enrollment_id: int
    role: str
    is_active: bool
    added_at: datetime
    # Nested user info from enrollment
    user_id: Optional[int] = None
    user_first_name: Optional[str] = None
    user_last_name: Optional[str] = None


class TeamMemberInit(BaseModel):
    """Schema for initial team member when creating a team."""
    enrollment_id: int
    role: str = Field("member", pattern="^(captain|member)$")


class TeamCreate(BaseModel):
    """Schema for creating a team."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    logo_url: Optional[str] = Field(None, max_length=500)
    members: Optional[List[TeamMemberInit]] = None  # Initial members to add


class TeamUpdate(BaseModel):
    """Schema for updating a team."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    logo_url: Optional[str] = Field(None, max_length=500)


class TeamResponse(BaseModel):
    """Schema for team response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    name: str
    team_number: Optional[int] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by_id: int
    # Creator info
    created_by_name: Optional[str] = None
    # Member count
    member_count: int = 0


class TeamDetailResponse(TeamResponse):
    """Schema for detailed team response with members."""
    members: List[TeamMemberResponse] = []


class TeamListResponse(BaseModel):
    """Schema for paginated team list with members."""
    items: List[TeamDetailResponse]
    total: int
    page: int
    page_size: int
    pages: int
