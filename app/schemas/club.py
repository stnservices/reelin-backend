"""Club schemas for request/response validation."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.club import MembershipRole, MembershipStatus


class ClubCreate(BaseModel):
    """Schema for creating a club."""

    name: str = Field(..., min_length=2, max_length=200)
    acronym: str = Field(..., min_length=1, max_length=20)
    description: Optional[str] = None
    logo_url: Optional[str] = None
    country_id: Optional[int] = None
    city_id: Optional[int] = None


class ClubUpdate(BaseModel):
    """Schema for updating a club."""

    name: Optional[str] = Field(None, min_length=2, max_length=200)
    acronym: Optional[str] = Field(None, min_length=1, max_length=20)
    description: Optional[str] = None
    logo_url: Optional[str] = None
    country_id: Optional[int] = None
    city_id: Optional[int] = None
    is_active: Optional[bool] = None


class ClubResponse(BaseModel):
    """Schema for club response."""

    id: int
    name: str
    acronym: str
    description: Optional[str] = None
    logo_url: Optional[str] = None
    country_id: Optional[int] = None
    city_id: Optional[int] = None
    owner_id: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ClubDetailResponse(ClubResponse):
    """Detailed club response with owner info."""

    owner_email: Optional[str] = None
    owner_first_name: Optional[str] = None
    owner_last_name: Optional[str] = None
    country_name: Optional[str] = None
    city_name: Optional[str] = None
    member_count: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_club(cls, club, member_count: int = 0) -> "ClubDetailResponse":
        """Create response from club model."""
        return cls(
            id=club.id,
            name=club.name,
            acronym=club.acronym,
            description=club.description,
            logo_url=club.logo_url,
            country_id=club.country_id,
            city_id=club.city_id,
            owner_id=club.owner_id,
            is_active=club.is_active,
            created_at=club.created_at,
            owner_email=club.owner.email if club.owner else None,
            owner_first_name=club.owner.profile.first_name if club.owner and club.owner.profile else None,
            owner_last_name=club.owner.profile.last_name if club.owner and club.owner.profile else None,
            country_name=club.country.name if club.country else None,
            city_name=club.city.name if club.city else None,
            member_count=member_count,
        )


class ClubListResponse(BaseModel):
    """Paginated club list response."""

    items: list[ClubDetailResponse]
    total: int
    page: int
    page_size: int
    pages: int


class MemberInvite(BaseModel):
    """Schema for inviting a member by email (GDPR compliant - no user search)."""

    email: EmailStr
    role: MembershipRole = MembershipRole.MEMBER
    permissions: dict = Field(default_factory=dict)


class MembershipUpdate(BaseModel):
    """Schema for updating a membership."""

    role: Optional[MembershipRole] = None
    permissions: Optional[dict] = None


class MembershipResponse(BaseModel):
    """Schema for membership response."""

    id: int
    club_id: int
    user_id: int
    role: str
    status: str
    permissions: dict
    invited_at: Optional[datetime] = None
    joined_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MembershipDetailResponse(MembershipResponse):
    """Detailed membership response with user info."""

    user_email: Optional[str] = None
    user_first_name: Optional[str] = None
    user_last_name: Optional[str] = None
    user_profile_picture_url: Optional[str] = None
    invited_by_email: Optional[str] = None
    invited_by_first_name: Optional[str] = None
    invited_by_last_name: Optional[str] = None
    dismissed_by_email: Optional[str] = None
    club_name: Optional[str] = None  # For invitation display

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_membership(cls, membership) -> "MembershipDetailResponse":
        """Create response from membership model."""
        return cls(
            id=membership.id,
            club_id=membership.club_id,
            user_id=membership.user_id,
            role=membership.role,
            status=membership.status,
            permissions=membership.permissions,
            invited_at=membership.invited_at,
            joined_at=membership.joined_at,
            dismissed_at=membership.dismissed_at,
            user_email=membership.user.email if membership.user else None,
            user_first_name=membership.user.profile.first_name if membership.user and membership.user.profile else None,
            user_last_name=membership.user.profile.last_name if membership.user and membership.user.profile else None,
            user_profile_picture_url=membership.user.profile.profile_picture_url if membership.user and membership.user.profile else None,
            invited_by_email=membership.invited_by.email if membership.invited_by else None,
            invited_by_first_name=membership.invited_by.profile.first_name if membership.invited_by and membership.invited_by.profile else None,
            invited_by_last_name=membership.invited_by.profile.last_name if membership.invited_by and membership.invited_by.profile else None,
            dismissed_by_email=membership.dismissed_by.email if membership.dismissed_by else None,
            club_name=membership.club.name if hasattr(membership, 'club') and membership.club else None,
        )


class MembershipListResponse(BaseModel):
    """Paginated membership list response."""

    items: list[MembershipDetailResponse]
    total: int
    page: int
    page_size: int
    pages: int
