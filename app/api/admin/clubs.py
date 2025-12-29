"""Admin club management endpoints."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.permissions import AdminOnly
from app.core.security import get_password_hash
from app.models.user import UserAccount, UserProfile
from app.models.club import Club, ClubMembership, MembershipStatus, MembershipRole
from app.schemas.club import ClubDetailResponse

router = APIRouter()


class AdminClubCreate(BaseModel):
    """Schema for admin club creation."""

    name: str = Field(..., min_length=2, max_length=200)
    acronym: str = Field(..., min_length=1, max_length=20)
    description: Optional[str] = None
    logo_url: Optional[str] = None
    country_id: Optional[int] = Field(None, description="Country ID for club location")
    city_id: Optional[int] = Field(None, description="City ID for club location")
    owner_id: int = Field(..., description="User ID of the organizer who will own this club")


class ValidatorPlaceholderCreate(BaseModel):
    """Schema for creating a placeholder validator account."""

    email: str = Field(..., description="Email for the placeholder validator")
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    password: str = Field(default="ChangeMe123!", description="Temporary password")


@router.post("/clubs", response_model=ClubDetailResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_club(
    club_data: AdminClubCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    Create a new club (admin only).

    The owner_id must be a user with the 'organizer' system role.
    The owner will automatically be added as an admin member of the club.
    """
    # Verify owner exists and has organizer role
    owner_query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == club_data.owner_id)
    )
    owner_result = await db.execute(owner_query)
    owner = owner_result.scalar_one_or_none()

    if not owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Owner user not found"
        )

    owner_roles = owner.profile.roles if owner.profile else []
    if "organizer" not in owner_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner must have the 'organizer' role"
        )

    # Check if name or acronym already exists
    existing_name = await db.execute(
        select(Club).where(Club.name == club_data.name, Club.is_deleted == False)
    )
    if existing_name.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Club with this name already exists"
        )

    existing_acronym = await db.execute(
        select(Club).where(Club.acronym == club_data.acronym.upper(), Club.is_deleted == False)
    )
    if existing_acronym.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Club with this acronym already exists"
        )

    # Create club
    club = Club(
        name=club_data.name,
        acronym=club_data.acronym.upper(),
        description=club_data.description,
        logo_url=club_data.logo_url,
        country_id=club_data.country_id,
        city_id=club_data.city_id,
        owner_id=club_data.owner_id,
        is_active=True,
    )
    db.add(club)
    await db.flush()

    # Add owner as admin member
    owner_membership = ClubMembership(
        club_id=club.id,
        user_id=club_data.owner_id,
        role=MembershipRole.ADMIN.value,
        status=MembershipStatus.ACTIVE.value,
        joined_at=datetime.now(timezone.utc),
        permissions={
            "can_create_events": True,
            "can_approve_members": True,
            "can_edit_club": True,
            "can_invite_members": True,
            "can_remove_members": True,
        },
    )
    db.add(owner_membership)

    await db.commit()
    await db.refresh(club)

    # Reload with owner, country, and city relationships
    query = (
        select(Club)
        .options(
            selectinload(Club.owner).selectinload(UserAccount.profile),
            selectinload(Club.country),
            selectinload(Club.city),
        )
        .where(Club.id == club.id)
    )
    result = await db.execute(query)
    club = result.scalar_one()

    return ClubDetailResponse.from_club(club, member_count=1)


@router.post("/clubs/{club_id}/create-validator", status_code=status.HTTP_201_CREATED)
async def create_placeholder_validator(
    club_id: int,
    validator_data: ValidatorPlaceholderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    Create a placeholder validator account and add to club.

    This creates a new user account with the 'validator' system role
    and automatically adds them as a club member with 'validator' club role.
    Useful for clubs that don't have real validator accounts yet.
    """
    # Check club exists
    club_query = select(Club).where(Club.id == club_id, Club.is_deleted == False)
    club_result = await db.execute(club_query)
    club = club_result.scalar_one_or_none()

    if not club:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Club not found"
        )

    # Check if email already exists
    existing_user = await db.execute(
        select(UserAccount).where(UserAccount.email == validator_data.email)
    )
    if existing_user.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists"
        )

    # Create user account
    user = UserAccount(
        email=validator_data.email,
        hashed_password=get_password_hash(validator_data.password),
        is_active=True,
        is_verified=True,  # Pre-verified since admin created
    )
    db.add(user)
    await db.flush()

    # Create user profile with validator role
    profile = UserProfile(
        user_id=user.id,
        first_name=validator_data.first_name,
        last_name=validator_data.last_name,
        roles=["angler", "validator"],  # Default angler + validator
    )
    db.add(profile)

    # Add as club member with validator role
    membership = ClubMembership(
        club_id=club_id,
        user_id=user.id,
        role=MembershipRole.VALIDATOR.value,
        status=MembershipStatus.ACTIVE.value,
        joined_at=datetime.now(timezone.utc),
        invited_by_id=current_user.id,
        invited_at=datetime.now(timezone.utc),
        permissions={
            "can_validate_catches": True,
        },
    )
    db.add(membership)

    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)

    return {
        "message": "Placeholder validator created and added to club",
        "user_id": user.id,
        "email": user.email,
        "first_name": profile.first_name,
        "last_name": profile.last_name,
        "club_id": club_id,
        "club_name": club.name,
        "note": "User should change their password on first login"
    }
