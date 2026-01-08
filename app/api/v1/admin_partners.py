"""Admin partners endpoints for managing landing page partners."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import UserAccount
from app.models.partner import Partner
from app.schemas.partner import PartnerCreate, PartnerUpdate, PartnerResponse
from app.core.permissions import AdminOnly

router = APIRouter()


@router.get("", response_model=List[PartnerResponse])
async def get_all_partners(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> List[Partner]:
    """
    Get all partners (including inactive).

    Admin only endpoint.
    """
    query = select(Partner).order_by(Partner.display_order, Partner.name)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("", response_model=PartnerResponse, status_code=201)
async def create_partner(
    partner_data: PartnerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> Partner:
    """
    Create a new partner.

    Admin only endpoint.
    """
    partner = Partner(**partner_data.model_dump())
    db.add(partner)
    await db.commit()
    await db.refresh(partner)
    return partner


@router.get("/{partner_id}", response_model=PartnerResponse)
async def get_partner(
    partner_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> Partner:
    """
    Get a specific partner by ID.

    Admin only endpoint.
    """
    result = await db.execute(select(Partner).where(Partner.id == partner_id))
    partner = result.scalar_one_or_none()

    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")

    return partner


@router.patch("/{partner_id}", response_model=PartnerResponse)
async def update_partner(
    partner_id: int,
    update_data: PartnerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> Partner:
    """
    Update a partner.

    Admin only endpoint. Only provided fields will be updated.
    """
    result = await db.execute(select(Partner).where(Partner.id == partner_id))
    partner = result.scalar_one_or_none()

    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")

    update_dict = update_data.model_dump(exclude_unset=True)

    if not update_dict:
        raise HTTPException(status_code=400, detail="No fields to update")

    for field, value in update_dict.items():
        setattr(partner, field, value)

    await db.commit()
    await db.refresh(partner)

    return partner


@router.delete("/{partner_id}", status_code=204)
async def delete_partner(
    partner_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> None:
    """
    Delete a partner.

    Admin only endpoint.
    """
    result = await db.execute(select(Partner).where(Partner.id == partner_id))
    partner = result.scalar_one_or_none()

    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")

    await db.delete(partner)
    await db.commit()
