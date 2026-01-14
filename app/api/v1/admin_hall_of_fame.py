"""Admin Hall of Fame endpoints for managing external achievements."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.database import get_db
from app.models.user import UserAccount
from app.models.hall_of_fame import HallOfFameEntry
from app.schemas.hall_of_fame import (
    HallOfFameCreate,
    HallOfFameUpdate,
    HallOfFameResponse,
    HallOfFameListResponse,
)
from app.core.permissions import AdminOnly

router = APIRouter()


@router.get("", response_model=HallOfFameListResponse)
async def get_all_hall_of_fame_entries(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    achievement_type: Optional[str] = None,
    format_code: Optional[str] = None,
    year: Optional[int] = None,
) -> HallOfFameListResponse:
    """
    Get all Hall of Fame entries with pagination and filtering.

    Admin only endpoint.
    """
    # Base query - load user with profile for name/avatar
    query = select(HallOfFameEntry).options(
        joinedload(HallOfFameEntry.user).selectinload(UserAccount.profile)
    )

    # Apply filters
    if achievement_type:
        query = query.where(HallOfFameEntry.achievement_type == achievement_type)
    if format_code:
        query = query.where(HallOfFameEntry.format_code == format_code)
    if year:
        query = query.where(HallOfFameEntry.competition_year == year)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination and ordering
    query = query.order_by(
        HallOfFameEntry.competition_year.desc(),
        HallOfFameEntry.achievement_type,
        HallOfFameEntry.position
    )
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    items = list(result.scalars().unique().all())

    return HallOfFameListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size
    )


@router.post("", response_model=HallOfFameResponse, status_code=201)
async def create_hall_of_fame_entry(
    entry_data: HallOfFameCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> HallOfFameEntry:
    """
    Create a new Hall of Fame entry.

    Admin only endpoint.
    """
    # If user_id provided, verify user exists
    if entry_data.user_id:
        user_result = await db.execute(
            select(UserAccount).where(UserAccount.id == entry_data.user_id)
        )
        if not user_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="User not found")

    entry = HallOfFameEntry(
        **entry_data.model_dump(),
        created_by_id=current_user.id
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    # Reload with user relationship and profile
    result = await db.execute(
        select(HallOfFameEntry)
        .options(joinedload(HallOfFameEntry.user).selectinload(UserAccount.profile))
        .where(HallOfFameEntry.id == entry.id)
    )
    return result.scalar_one()


@router.get("/{entry_id}", response_model=HallOfFameResponse)
async def get_hall_of_fame_entry(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> HallOfFameEntry:
    """
    Get a specific Hall of Fame entry by ID.

    Admin only endpoint.
    """
    result = await db.execute(
        select(HallOfFameEntry)
        .options(joinedload(HallOfFameEntry.user).selectinload(UserAccount.profile))
        .where(HallOfFameEntry.id == entry_id)
    )
    entry = result.scalar_one_or_none()

    if not entry:
        raise HTTPException(status_code=404, detail="Hall of Fame entry not found")

    return entry


@router.patch("/{entry_id}", response_model=HallOfFameResponse)
async def update_hall_of_fame_entry(
    entry_id: int,
    update_data: HallOfFameUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> HallOfFameEntry:
    """
    Update a Hall of Fame entry.

    Admin only endpoint. Only provided fields will be updated.
    """
    result = await db.execute(
        select(HallOfFameEntry).where(HallOfFameEntry.id == entry_id)
    )
    entry = result.scalar_one_or_none()

    if not entry:
        raise HTTPException(status_code=404, detail="Hall of Fame entry not found")

    update_dict = update_data.model_dump(exclude_unset=True)

    if not update_dict:
        raise HTTPException(status_code=400, detail="No fields to update")

    # If user_id provided, verify user exists
    if "user_id" in update_dict and update_dict["user_id"]:
        user_result = await db.execute(
            select(UserAccount).where(UserAccount.id == update_dict["user_id"])
        )
        if not user_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="User not found")

    for field, value in update_dict.items():
        setattr(entry, field, value)

    await db.commit()

    # Reload with user relationship and profile
    result = await db.execute(
        select(HallOfFameEntry)
        .options(joinedload(HallOfFameEntry.user).selectinload(UserAccount.profile))
        .where(HallOfFameEntry.id == entry.id)
    )
    return result.scalar_one()


@router.delete("/{entry_id}", status_code=204)
async def delete_hall_of_fame_entry(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> None:
    """
    Delete a Hall of Fame entry.

    Admin only endpoint.
    """
    result = await db.execute(
        select(HallOfFameEntry).where(HallOfFameEntry.id == entry_id)
    )
    entry = result.scalar_one_or_none()

    if not entry:
        raise HTTPException(status_code=404, detail="Hall of Fame entry not found")

    await db.delete(entry)
    await db.commit()
