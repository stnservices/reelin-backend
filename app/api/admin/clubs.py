"""Admin club management endpoints."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func
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


class AdminClubUpdate(BaseModel):
    """Schema for admin club updates including owner transfer."""

    name: Optional[str] = Field(None, min_length=2, max_length=200)
    acronym: Optional[str] = Field(None, min_length=1, max_length=20)
    description: Optional[str] = None
    logo_url: Optional[str] = None
    country_id: Optional[int] = None
    city_id: Optional[int] = None
    owner_id: Optional[int] = Field(None, description="New owner user ID (must have organizer role)")
    is_active: Optional[bool] = None


class ValidatorPlaceholderCreate(BaseModel):
    """Schema for creating a placeholder validator account."""

    email: str = Field(..., description="Email for the placeholder validator")
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    password: str = Field(default="ChangeMe123!", description="Temporary password")

    @field_validator("first_name")
    @classmethod
    def normalize_first_name(cls, v: str) -> str:
        """Normalize first name to Title Case."""
        return v.strip().title()

    @field_validator("last_name")
    @classmethod
    def normalize_last_name(cls, v: str) -> str:
        """Normalize last name to UPPERCASE."""
        return v.strip().upper()


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


@router.patch("/clubs/{club_id}", response_model=ClubDetailResponse)
async def admin_update_club(
    club_id: int,
    update_data: AdminClubUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    Update a club including transferring ownership (admin only).

    If owner_id is provided, the club ownership will be transferred to the new user.
    The new owner must have the 'organizer' system role.
    """
    # Get the club
    club_query = (
        select(Club)
        .options(selectinload(Club.owner).selectinload(UserAccount.profile))
        .where(Club.id == club_id, Club.is_deleted == False)
    )
    club_result = await db.execute(club_query)
    club = club_result.scalar_one_or_none()

    if not club:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Club not found"
        )

    old_owner_id = club.owner_id

    # Handle ownership transfer
    if update_data.owner_id is not None and update_data.owner_id != club.owner_id:
        # Verify new owner exists and has organizer role
        new_owner_query = (
            select(UserAccount)
            .options(selectinload(UserAccount.profile))
            .where(UserAccount.id == update_data.owner_id)
        )
        new_owner_result = await db.execute(new_owner_query)
        new_owner = new_owner_result.scalar_one_or_none()

        if not new_owner:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="New owner user not found"
            )

        new_owner_roles = new_owner.profile.roles if new_owner.profile else []
        if "organizer" not in new_owner_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New owner must have the 'organizer' role"
            )

        # Check if new owner already owns another club
        existing_club_query = select(Club).where(
            Club.owner_id == update_data.owner_id,
            Club.id != club_id,
            Club.is_deleted == False
        )
        existing_club_result = await db.execute(existing_club_query)
        if existing_club_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="New owner already owns another club"
            )

        # Transfer ownership
        club.owner_id = update_data.owner_id

        # Update memberships:
        # 1. Demote old owner from admin (if they have a membership)
        old_owner_membership_query = select(ClubMembership).where(
            ClubMembership.club_id == club_id,
            ClubMembership.user_id == old_owner_id,
            ClubMembership.status == MembershipStatus.ACTIVE.value,
        )
        old_owner_membership_result = await db.execute(old_owner_membership_query)
        old_owner_membership = old_owner_membership_result.scalar_one_or_none()

        if old_owner_membership:
            # Keep them as a regular member
            old_owner_membership.role = MembershipRole.MEMBER.value

        # 2. Add or promote new owner to admin
        new_owner_membership_query = select(ClubMembership).where(
            ClubMembership.club_id == club_id,
            ClubMembership.user_id == update_data.owner_id,
        )
        new_owner_membership_result = await db.execute(new_owner_membership_query)
        new_owner_membership = new_owner_membership_result.scalar_one_or_none()

        if new_owner_membership:
            # Promote to admin if not already
            new_owner_membership.role = MembershipRole.ADMIN.value
            new_owner_membership.status = MembershipStatus.ACTIVE.value
            if not new_owner_membership.joined_at:
                new_owner_membership.joined_at = datetime.now(timezone.utc)
        else:
            # Create new admin membership
            new_membership = ClubMembership(
                club_id=club_id,
                user_id=update_data.owner_id,
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
            db.add(new_membership)

    # Update other fields
    if update_data.name is not None:
        # Check uniqueness
        existing = await db.execute(
            select(Club).where(
                Club.name == update_data.name,
                Club.id != club_id,
                Club.is_deleted == False,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Club name already exists"
            )
        club.name = update_data.name

    if update_data.acronym is not None:
        existing = await db.execute(
            select(Club).where(
                Club.acronym == update_data.acronym.upper(),
                Club.id != club_id,
                Club.is_deleted == False,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Club acronym already exists"
            )
        club.acronym = update_data.acronym.upper()

    if update_data.description is not None:
        club.description = update_data.description

    if update_data.logo_url is not None:
        club.logo_url = update_data.logo_url

    if update_data.country_id is not None:
        club.country_id = update_data.country_id if update_data.country_id > 0 else None

    if update_data.city_id is not None:
        club.city_id = update_data.city_id if update_data.city_id > 0 else None

    if update_data.is_active is not None:
        club.is_active = update_data.is_active

    await db.commit()

    # Reload with all relationships
    query = (
        select(Club)
        .options(
            selectinload(Club.owner).selectinload(UserAccount.profile),
            selectinload(Club.country),
            selectinload(Club.city),
        )
        .where(Club.id == club_id)
    )
    result = await db.execute(query)
    club = result.scalar_one()

    # Get member count
    member_count_query = select(func.count(ClubMembership.id)).where(
        ClubMembership.club_id == club.id,
        ClubMembership.status == MembershipStatus.ACTIVE.value,
    )
    member_count_result = await db.execute(member_count_query)
    member_count = member_count_result.scalar()

    return ClubDetailResponse.from_club(club, member_count)


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
