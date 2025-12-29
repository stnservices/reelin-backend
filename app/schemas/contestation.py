"""Contestation schemas for request/response validation."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.contestation import ContestationStatus, ContestationType


class ContestationCreate(BaseModel):
    """Schema for submitting a new contestation."""

    reported_user_id: Optional[int] = Field(
        None, description="User being reported (optional for general reports)"
    )
    reported_catch_id: Optional[int] = Field(
        None, description="Catch being disputed (optional)"
    )
    contestation_type: ContestationType = Field(
        ..., description="Type of contestation"
    )
    title: str = Field(
        ..., min_length=5, max_length=200, description="Brief title of the report"
    )
    description: str = Field(
        ..., min_length=20, max_length=2000, description="Detailed description"
    )
    evidence_url: Optional[str] = Field(
        None, description="URL to photo/video evidence"
    )


class ContestationUpdate(BaseModel):
    """Schema for updating a pending contestation."""

    title: Optional[str] = Field(
        None, min_length=5, max_length=200, description="Brief title of the report"
    )
    description: Optional[str] = Field(
        None, min_length=20, max_length=2000, description="Detailed description"
    )
    evidence_url: Optional[str] = Field(
        None, description="URL to photo/video evidence"
    )


class ContestationReview(BaseModel):
    """Schema for organizer to review a contestation."""

    status: Literal["approved", "rejected"] = Field(
        ..., description="Review decision"
    )
    review_notes: Optional[str] = Field(
        None, max_length=500, description="Notes explaining the decision"
    )
    penalty_points: int = Field(
        0, ge=0, le=1000, description="Penalty points to apply to reported user"
    )


class ContestationResponse(BaseModel):
    """
    Schema for contestation response (anonymous view for participants).

    Does NOT include reporter identity to protect anonymity.
    """

    id: int
    event_id: int
    contestation_type: str
    title: str
    description: str
    evidence_url: Optional[str] = None
    status: str
    penalty_points_applied: int
    created_at: datetime
    updated_at: datetime
    # Reported user/catch info (visible to all)
    reported_user_id: Optional[int] = None
    reported_user_name: Optional[str] = None
    reported_catch_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_contestation(cls, contestation, include_reported_name: bool = True) -> "ContestationResponse":
        """Create anonymous response from contestation model."""
        reported_name = None
        if include_reported_name and contestation.reported_user:
            profile = contestation.reported_user.profile
            if profile and (profile.first_name or profile.last_name):
                reported_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip()
            else:
                reported_name = contestation.reported_user.email

        return cls(
            id=contestation.id,
            event_id=contestation.event_id,
            contestation_type=contestation.contestation_type,
            title=contestation.title,
            description=contestation.description,
            evidence_url=contestation.evidence_url,
            status=contestation.status,
            penalty_points_applied=contestation.penalty_points_applied,
            created_at=contestation.created_at,
            updated_at=contestation.updated_at,
            reported_user_id=contestation.reported_user_id,
            reported_user_name=reported_name,
            reported_catch_id=contestation.reported_catch_id,
        )


class ContestationDetailResponse(ContestationResponse):
    """
    Detailed contestation response for organizers.

    Includes reporter identity and review information.
    """

    # Reporter info (only visible to organizers)
    reporter_user_id: int
    reporter_name: Optional[str] = None
    reporter_email: Optional[str] = None
    # Review info
    reviewed_by_id: Optional[int] = None
    reviewed_by_email: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None

    @classmethod
    def from_contestation(cls, contestation) -> "ContestationDetailResponse":
        """Create detailed response from contestation model."""
        # Reporter name
        reporter_name = None
        reporter_email = None
        if contestation.reporter:
            reporter_email = contestation.reporter.email
            profile = contestation.reporter.profile
            if profile and (profile.first_name or profile.last_name):
                reporter_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip()

        # Reported user name
        reported_name = None
        if contestation.reported_user:
            profile = contestation.reported_user.profile
            if profile and (profile.first_name or profile.last_name):
                reported_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip()
            else:
                reported_name = contestation.reported_user.email

        # Reviewer email
        reviewer_email = None
        if contestation.reviewed_by:
            reviewer_email = contestation.reviewed_by.email

        return cls(
            id=contestation.id,
            event_id=contestation.event_id,
            contestation_type=contestation.contestation_type,
            title=contestation.title,
            description=contestation.description,
            evidence_url=contestation.evidence_url,
            status=contestation.status,
            penalty_points_applied=contestation.penalty_points_applied,
            created_at=contestation.created_at,
            updated_at=contestation.updated_at,
            reported_user_id=contestation.reported_user_id,
            reported_user_name=reported_name,
            reported_catch_id=contestation.reported_catch_id,
            reporter_user_id=contestation.reporter_user_id,
            reporter_name=reporter_name,
            reporter_email=reporter_email,
            reviewed_by_id=contestation.reviewed_by_id,
            reviewed_by_email=reviewer_email,
            reviewed_at=contestation.reviewed_at,
            review_notes=contestation.review_notes,
        )


class ContestationListResponse(BaseModel):
    """Paginated contestation list response."""

    items: list[ContestationResponse]
    total: int
    page: int
    page_size: int
    pages: int


class ContestationDetailListResponse(BaseModel):
    """Paginated contestation list response with full details (for organizers)."""

    items: list[ContestationDetailResponse]
    total: int
    page: int
    page_size: int
    pages: int
