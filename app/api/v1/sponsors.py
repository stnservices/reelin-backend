"""Sponsor endpoints for organizers and event creation."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_optional
from app.models.sponsor import Sponsor, SponsorTier, TIER_ORDER
from app.models.user import UserAccount

router = APIRouter()


# Schemas
class SponsorCreate(BaseModel):
    """Schema for creating a sponsor."""
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    logo_url: Optional[str] = None
    website_url: Optional[str] = None
    contact_email: Optional[str] = None
    tier: str = Field(default=SponsorTier.PARTNER.value)
    display_order: int = 0


class SponsorUpdate(BaseModel):
    """Schema for updating a sponsor."""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    logo_url: Optional[str] = None
    website_url: Optional[str] = None
    contact_email: Optional[str] = None
    tier: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


def sponsor_to_dict(s: Sponsor, include_owner: bool = False) -> dict:
    """Convert sponsor to response dict."""
    result = {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "logo_url": s.logo_url,
        "website_url": s.website_url,
        "contact_email": s.contact_email,
        "tier": s.tier,
        "display_order": s.display_order,
        "is_active": s.is_active,
        "is_global": s.owner_id is None,
    }
    if include_owner:
        result["owner_id"] = s.owner_id
        result["owner_email"] = s.owner.email if s.owner else None
    return result


@router.get("")
async def list_sponsors(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[UserAccount] = Depends(get_current_user_optional),
):
    """List sponsors available for event creation.

    For authenticated users: returns user's own sponsors + global sponsors.
    For unauthenticated: returns only global sponsors.
    """
    if current_user:
        # User's sponsors + global sponsors
        query = (
            select(Sponsor)
            .where(
                Sponsor.is_active == True,
                or_(
                    Sponsor.owner_id == current_user.id,
                    Sponsor.owner_id.is_(None)
                )
            )
            .order_by(Sponsor.display_order, Sponsor.name)
        )
    else:
        # Only global sponsors
        query = (
            select(Sponsor)
            .where(Sponsor.is_active == True, Sponsor.owner_id.is_(None))
            .order_by(Sponsor.display_order, Sponsor.name)
        )

    result = await db.execute(query)
    sponsors = result.scalars().all()

    # Sort by tier priority
    sorted_sponsors = sorted(
        sponsors,
        key=lambda s: (TIER_ORDER.get(SponsorTier(s.tier) if s.tier else SponsorTier.PARTNER, 99), s.display_order, s.name)
    )

    return [sponsor_to_dict(s) for s in sorted_sponsors]


@router.get("/my")
async def list_my_sponsors(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """List only the current user's own sponsors (for management)."""
    query = (
        select(Sponsor)
        .where(Sponsor.owner_id == current_user.id)
        .order_by(Sponsor.display_order, Sponsor.name)
    )
    result = await db.execute(query)
    sponsors = result.scalars().all()

    # Sort by tier priority
    sorted_sponsors = sorted(
        sponsors,
        key=lambda s: (TIER_ORDER.get(SponsorTier(s.tier) if s.tier else SponsorTier.PARTNER, 99), s.display_order, s.name)
    )

    return [sponsor_to_dict(s, include_owner=True) for s in sorted_sponsors]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_sponsor(
    sponsor_data: SponsorCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """Create a new sponsor owned by the current user.

    Organizers can create their own sponsors for use in their events.
    """
    # Check if user has organizer role
    user_roles = current_user.profile.roles if current_user.profile else []
    if "organizer" not in user_roles and "administrator" not in user_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organizers can create sponsors"
        )

    # Check for duplicate name for this owner
    existing = await db.execute(
        select(Sponsor).where(
            Sponsor.name == sponsor_data.name,
            Sponsor.owner_id == current_user.id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You already have a sponsor with this name"
        )

    # Create sponsor
    sponsor = Sponsor(
        name=sponsor_data.name,
        description=sponsor_data.description,
        logo_url=sponsor_data.logo_url,
        website_url=sponsor_data.website_url,
        contact_email=sponsor_data.contact_email,
        tier=sponsor_data.tier,
        display_order=sponsor_data.display_order,
        owner_id=current_user.id,
    )
    db.add(sponsor)
    await db.commit()
    await db.refresh(sponsor)

    return sponsor_to_dict(sponsor, include_owner=True)


@router.patch("/{sponsor_id}")
async def update_sponsor(
    sponsor_id: int,
    sponsor_data: SponsorUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """Update a sponsor. Only owner or admin can update."""
    # Get sponsor
    result = await db.execute(select(Sponsor).where(Sponsor.id == sponsor_id))
    sponsor = result.scalar_one_or_none()

    if not sponsor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sponsor not found"
        )

    # Check permissions
    user_roles = current_user.profile.roles if current_user.profile else []
    is_admin = "administrator" in user_roles
    is_owner = sponsor.owner_id == current_user.id

    if not is_admin and not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit your own sponsors"
        )

    # Check for duplicate name if name is being changed
    if sponsor_data.name and sponsor_data.name != sponsor.name:
        existing = await db.execute(
            select(Sponsor).where(
                Sponsor.name == sponsor_data.name,
                Sponsor.owner_id == sponsor.owner_id,
                Sponsor.id != sponsor_id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A sponsor with this name already exists"
            )

    # Update fields
    update_data = sponsor_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(sponsor, field, value)

    await db.commit()
    await db.refresh(sponsor)

    return sponsor_to_dict(sponsor, include_owner=True)


@router.delete("/{sponsor_id}")
async def delete_sponsor(
    sponsor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """Delete a sponsor. Only owner or admin can delete."""
    # Get sponsor
    result = await db.execute(select(Sponsor).where(Sponsor.id == sponsor_id))
    sponsor = result.scalar_one_or_none()

    if not sponsor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sponsor not found"
        )

    # Check permissions
    user_roles = current_user.profile.roles if current_user.profile else []
    is_admin = "administrator" in user_roles
    is_owner = sponsor.owner_id == current_user.id

    if not is_admin and not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own sponsors"
        )

    # Soft delete by deactivating
    sponsor.is_active = False
    await db.commit()

    return {"message": "Sponsor deleted successfully"}


@router.get("/grouped")
async def list_sponsors_grouped(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[UserAccount] = Depends(get_current_user_optional),
):
    """List active sponsors grouped by tier for event pages.

    For authenticated users: returns user's own sponsors + global sponsors.
    """
    if current_user:
        query = (
            select(Sponsor)
            .where(
                Sponsor.is_active == True,
                or_(
                    Sponsor.owner_id == current_user.id,
                    Sponsor.owner_id.is_(None)
                )
            )
            .order_by(Sponsor.display_order, Sponsor.name)
        )
    else:
        query = (
            select(Sponsor)
            .where(Sponsor.is_active == True, Sponsor.owner_id.is_(None))
            .order_by(Sponsor.display_order, Sponsor.name)
        )

    result = await db.execute(query)
    sponsors = result.scalars().all()

    # Group by tier
    grouped = {tier.value: [] for tier in SponsorTier}
    for sponsor in sponsors:
        tier_key = sponsor.tier if sponsor.tier in grouped else SponsorTier.PARTNER.value
        grouped[tier_key].append({
            "id": sponsor.id,
            "name": sponsor.name,
            "logo_url": sponsor.logo_url,
            "website_url": sponsor.website_url,
            "tier": sponsor.tier,
            "is_global": sponsor.owner_id is None,
        })

    return {
        "tiers": [
            {"tier": tier.value, "name": tier.value.capitalize(), "sponsors": grouped[tier.value]}
            for tier in SponsorTier
        ]
    }
