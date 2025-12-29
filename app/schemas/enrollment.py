"""Enrollment schemas for request/response validation."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enrollment import EnrollmentStatus


class EnrollmentUserResponse(BaseModel):
    """Minimal user info for enrollment responses."""

    id: int
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class EnrollmentCreate(BaseModel):
    """Schema for creating an enrollment."""

    event_id: int


class EnrollmentUpdate(BaseModel):
    """Schema for updating an enrollment (approval/rejection)."""

    status: EnrollmentStatus
    draw_number: Optional[int] = None
    rejection_reason: Optional[str] = None


class EnrollmentResponse(BaseModel):
    """Schema for enrollment response."""

    id: int
    event_id: int
    user_id: int
    status: str
    draw_number: Optional[int] = None
    enrolled_at: datetime
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    # Disqualification fields
    disqualified_at: Optional[datetime] = None
    disqualification_reason: Optional[str] = None
    reinstated_at: Optional[datetime] = None
    reinstatement_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class EnrollmentDetailResponse(EnrollmentResponse):
    """Detailed enrollment response with user info."""

    user_email: Optional[str] = None
    user_first_name: Optional[str] = None
    user_last_name: Optional[str] = None
    user_phone: Optional[str] = None
    user_profile_picture_url: Optional[str] = None
    user_is_pro: bool = False
    approved_by_email: Optional[str] = None
    disqualified_by_email: Optional[str] = None
    reinstated_by_email: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_enrollment(cls, enrollment) -> "EnrollmentDetailResponse":
        """Create response from enrollment model with nested relationships."""
        return cls(
            id=enrollment.id,
            event_id=enrollment.event_id,
            user_id=enrollment.user_id,
            status=enrollment.status,
            draw_number=enrollment.draw_number,
            enrolled_at=enrollment.enrolled_at,
            approved_at=enrollment.approved_at,
            rejection_reason=enrollment.rejection_reason,
            disqualified_at=enrollment.disqualified_at,
            disqualification_reason=enrollment.disqualification_reason,
            reinstated_at=enrollment.reinstated_at,
            reinstatement_reason=enrollment.reinstatement_reason,
            user_email=enrollment.user.email if enrollment.user else None,
            user_first_name=enrollment.user.profile.first_name if enrollment.user and enrollment.user.profile else None,
            user_last_name=enrollment.user.profile.last_name if enrollment.user and enrollment.user.profile else None,
            user_phone=enrollment.user.profile.phone if enrollment.user and enrollment.user.profile else None,
            user_profile_picture_url=enrollment.user.profile.profile_picture_url if enrollment.user and enrollment.user.profile else None,
            user_is_pro=enrollment.user.is_pro if enrollment.user else False,
            approved_by_email=enrollment.approved_by.email if enrollment.approved_by else None,
            disqualified_by_email=enrollment.disqualified_by.email if enrollment.disqualified_by else None,
            reinstated_by_email=enrollment.reinstated_by.email if enrollment.reinstated_by else None,
        )


class EnrollmentListResponse(BaseModel):
    """Paginated enrollment list response."""

    items: list[EnrollmentDetailResponse]
    total: int
    page: int
    page_size: int
    pages: int


# Disqualification schemas
class DisqualifyRequest(BaseModel):
    """Schema for disqualifying a participant."""

    reason: str = Field(..., min_length=5, max_length=500, description="Reason for disqualification (required)")


class ReinstateRequest(BaseModel):
    """Schema for reinstating a disqualified participant."""

    reason: str = Field(..., min_length=5, max_length=500, description="Reason for reinstatement (required)")


# Ban schemas
class EventBanCreate(BaseModel):
    """Schema for banning a user from an event."""

    user_id: int
    reason: Optional[str] = Field(None, max_length=500)


class EventBanResponse(BaseModel):
    """Schema for event ban response."""

    id: int
    event_id: int
    user_id: int
    reason: Optional[str] = None
    banned_at: datetime
    user_email: Optional[str] = None
    user_first_name: Optional[str] = None
    user_last_name: Optional[str] = None
    banned_by_email: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_ban(cls, ban) -> "EventBanResponse":
        """Create response from ban model."""
        return cls(
            id=ban.id,
            event_id=ban.event_id,
            user_id=ban.user_id,
            reason=ban.reason,
            banned_at=ban.banned_at,
            user_email=ban.user.email if ban.user else None,
            user_first_name=ban.user.profile.first_name if ban.user and ban.user.profile else None,
            user_last_name=ban.user.profile.last_name if ban.user and ban.user.profile else None,
            banned_by_email=ban.banned_by.email if ban.banned_by else None,
        )


# Admin enrollment schemas
class AdminEnrollRequest(BaseModel):
    """Schema for admin/organizer enrolling a user by email."""

    user_email: EmailStr = Field(..., description="Email address of the user to enroll")
    approve_immediately: bool = Field(
        default=True,
        description="If true, enrollment is directly approved. If false, set to pending."
    )
