"""Location endpoints (countries, cities, fishing spots, meeting points) with cascade support."""

from typing import Optional, List
from math import ceil

from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models import UserAccount
from app.models.location import Country, City, FishingSpot, MeetingPoint

router = APIRouter()


# ============== Schemas ==============

class FishingSpotCreate(BaseModel):
    city_id: int
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    address: Optional[str] = Field(None, max_length=500)


class FishingSpotUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    address: Optional[str] = Field(None, max_length=500)


class FishingSpotResponse(BaseModel):
    id: int
    city_id: int
    owner_id: Optional[int]
    name: str
    description: Optional[str]
    latitude: float
    longitude: float
    address: Optional[str]
    city_name: Optional[str]
    country_name: Optional[str]
    is_mine: bool = False

    class Config:
        from_attributes = True


class MeetingPointCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    address: Optional[str] = Field(None, max_length=500)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    description: Optional[str] = Field(None, max_length=2000)


class MeetingPointUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    address: Optional[str] = Field(None, max_length=500)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    description: Optional[str] = Field(None, max_length=2000)


class MeetingPointResponse(BaseModel):
    id: int
    fishing_spot_id: int
    owner_id: Optional[int]
    name: str
    address: Optional[str]
    latitude: float
    longitude: float
    description: Optional[str]
    is_mine: bool = False

    class Config:
        from_attributes = True


@router.get("/countries")
async def list_countries(
    db: AsyncSession = Depends(get_db),
):
    """List all countries for dropdown selection."""
    query = select(Country).order_by(Country.name)
    result = await db.execute(query)
    countries = result.scalars().all()
    return [{"id": c.id, "name": c.name, "code": c.code} for c in countries]


@router.get("/countries/{country_id}/cities")
async def list_cities_by_country(
    country_id: int,
    search: Optional[str] = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db),
):
    """List cities for a country with optional search."""
    query = select(City).where(City.country_id == country_id)
    if search:
        query = query.where(City.name.ilike(f"%{search}%"))
    query = query.order_by(City.name).limit(50)
    result = await db.execute(query)
    cities = result.scalars().all()
    return [{"id": c.id, "name": c.name, "country_id": c.country_id} for c in cities]


@router.get("/cities")
async def list_all_cities(
    country_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db),
):
    """List all cities with optional country filter and search."""
    query = select(City).options(selectinload(City.country))
    if country_id:
        query = query.where(City.country_id == country_id)
    if search:
        query = query.where(City.name.ilike(f"%{search}%"))
    query = query.order_by(City.name).limit(50)
    result = await db.execute(query)
    cities = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "country_id": c.country_id,
            "country_name": c.country.name if c.country else None,
        }
        for c in cities
    ]


@router.get("/cities/{city_id}/fishing-spots")
async def list_fishing_spots_by_city(
    city_id: int,
    search: Optional[str] = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db),
):
    """List fishing spots for a city with optional search."""
    query = select(FishingSpot).where(FishingSpot.city_id == city_id)
    if search:
        query = query.where(FishingSpot.name.ilike(f"%{search}%"))
    query = query.order_by(FishingSpot.name).limit(50)
    result = await db.execute(query)
    spots = result.scalars().all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "latitude": s.latitude,
            "longitude": s.longitude,
            "city_id": s.city_id,
        }
        for s in spots
    ]


@router.get("/fishing-spots")
async def list_fishing_spots(
    city_id: Optional[int] = Query(None),
    country_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db),
):
    """
    List/search fishing spots with cascade filters.
    - Filter by city_id for specific city
    - Filter by country_id to get all spots in a country
    - Search by name across filters
    """
    query = (
        select(FishingSpot)
        .options(selectinload(FishingSpot.city).selectinload(City.country))
    )

    if city_id:
        query = query.where(FishingSpot.city_id == city_id)
    elif country_id:
        query = query.join(City).where(City.country_id == country_id)

    if search:
        query = query.where(FishingSpot.name.ilike(f"%{search}%"))

    query = query.order_by(FishingSpot.name).limit(50)
    result = await db.execute(query)
    spots = result.scalars().all()

    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "latitude": s.latitude,
            "longitude": s.longitude,
            "city_id": s.city_id,
            "city_name": s.city.name if s.city else None,
            "country_name": s.city.country.name if s.city and s.city.country else None,
        }
        for s in spots
    ]


@router.get("/fishing-spots/{spot_id}")
async def get_fishing_spot(
    spot_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get a single fishing spot with full location details."""
    query = (
        select(FishingSpot)
        .options(selectinload(FishingSpot.city).selectinload(City.country))
        .where(FishingSpot.id == spot_id)
    )
    result = await db.execute(query)
    spot = result.scalar_one_or_none()

    if not spot:
        raise HTTPException(status_code=404, detail="Fishing spot not found")

    return {
        "id": spot.id,
        "name": spot.name,
        "description": spot.description,
        "latitude": spot.latitude,
        "longitude": spot.longitude,
        "city_id": spot.city_id,
        "city_name": spot.city.name if spot.city else None,
        "country_id": spot.city.country_id if spot.city else None,
        "country_name": spot.city.country.name if spot.city and spot.city.country else None,
    }


# ============== My Fishing Spots (Organizer CRUD) ==============


@router.get("/my/fishing-spots")
async def list_my_fishing_spots(
    city_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None, min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List fishing spots owned by current user.
    Also includes global spots they can use.
    """
    query = (
        select(FishingSpot)
        .options(selectinload(FishingSpot.city).selectinload(City.country))
        .where(
            or_(
                FishingSpot.owner_id == current_user.id,
                FishingSpot.owner_id.is_(None)  # Global spots
            )
        )
    )

    if city_id:
        query = query.where(FishingSpot.city_id == city_id)

    if search:
        query = query.where(FishingSpot.name.ilike(f"%{search}%"))

    # Count total
    count_query = select(func.count(FishingSpot.id)).where(
        or_(
            FishingSpot.owner_id == current_user.id,
            FishingSpot.owner_id.is_(None)
        )
    )
    if city_id:
        count_query = count_query.where(FishingSpot.city_id == city_id)
    if search:
        count_query = count_query.where(FishingSpot.name.ilike(f"%{search}%"))

    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Order by owner (mine first), then name
    query = query.order_by(
        FishingSpot.owner_id.is_(None).asc(),  # User's spots first
        FishingSpot.name
    )
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    spots = result.scalars().all()

    return {
        "items": [
            {
                "id": s.id,
                "city_id": s.city_id,
                "owner_id": s.owner_id,
                "name": s.name,
                "description": s.description,
                "latitude": s.latitude,
                "longitude": s.longitude,
                "address": s.address,
                "city_name": s.city.name if s.city else None,
                "country_name": s.city.country.name if s.city and s.city.country else None,
                "is_mine": s.owner_id == current_user.id,
            }
            for s in spots
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": ceil(total / page_size) if total > 0 else 1,
    }


@router.post("/my/fishing-spots", response_model=FishingSpotResponse, status_code=status.HTTP_201_CREATED)
async def create_fishing_spot(
    data: FishingSpotCreate,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new fishing spot owned by current user."""
    # Verify city exists
    city = await db.get(City, data.city_id)
    if not city:
        raise HTTPException(status_code=404, detail="City not found")

    spot = FishingSpot(
        city_id=data.city_id,
        owner_id=current_user.id,
        name=data.name,
        description=data.description,
        latitude=data.latitude,
        longitude=data.longitude,
        address=data.address,
    )
    db.add(spot)
    await db.commit()
    await db.refresh(spot)

    # Load relationships
    await db.refresh(spot, ["city"])

    return FishingSpotResponse(
        id=spot.id,
        city_id=spot.city_id,
        owner_id=spot.owner_id,
        name=spot.name,
        description=spot.description,
        latitude=spot.latitude,
        longitude=spot.longitude,
        address=spot.address,
        city_name=spot.city.name if spot.city else None,
        country_name=spot.city.country.name if spot.city and spot.city.country else None,
        is_mine=True,
    )


@router.get("/my/fishing-spots/{spot_id}", response_model=FishingSpotResponse)
async def get_my_fishing_spot(
    spot_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a fishing spot by ID (must be owned by user or global)."""
    query = (
        select(FishingSpot)
        .options(selectinload(FishingSpot.city).selectinload(City.country))
        .where(
            FishingSpot.id == spot_id,
            or_(
                FishingSpot.owner_id == current_user.id,
                FishingSpot.owner_id.is_(None)
            )
        )
    )
    result = await db.execute(query)
    spot = result.scalar_one_or_none()

    if not spot:
        raise HTTPException(status_code=404, detail="Fishing spot not found")

    return FishingSpotResponse(
        id=spot.id,
        city_id=spot.city_id,
        owner_id=spot.owner_id,
        name=spot.name,
        description=spot.description,
        latitude=spot.latitude,
        longitude=spot.longitude,
        address=spot.address,
        city_name=spot.city.name if spot.city else None,
        country_name=spot.city.country.name if spot.city and spot.city.country else None,
        is_mine=spot.owner_id == current_user.id,
    )


@router.patch("/my/fishing-spots/{spot_id}", response_model=FishingSpotResponse)
async def update_fishing_spot(
    spot_id: int,
    data: FishingSpotUpdate,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a fishing spot (must be owned by user)."""
    query = (
        select(FishingSpot)
        .options(selectinload(FishingSpot.city).selectinload(City.country))
        .where(
            FishingSpot.id == spot_id,
            FishingSpot.owner_id == current_user.id  # Only owner can edit
        )
    )
    result = await db.execute(query)
    spot = result.scalar_one_or_none()

    if not spot:
        raise HTTPException(status_code=404, detail="Fishing spot not found or not owned by you")

    # Update fields
    update_dict = data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        setattr(spot, field, value)

    await db.commit()
    await db.refresh(spot)

    return FishingSpotResponse(
        id=spot.id,
        city_id=spot.city_id,
        owner_id=spot.owner_id,
        name=spot.name,
        description=spot.description,
        latitude=spot.latitude,
        longitude=spot.longitude,
        address=spot.address,
        city_name=spot.city.name if spot.city else None,
        country_name=spot.city.country.name if spot.city and spot.city.country else None,
        is_mine=True,
    )


@router.delete("/my/fishing-spots/{spot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fishing_spot(
    spot_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a fishing spot (must be owned by user)."""
    query = select(FishingSpot).where(
        FishingSpot.id == spot_id,
        FishingSpot.owner_id == current_user.id
    )
    result = await db.execute(query)
    spot = result.scalar_one_or_none()

    if not spot:
        raise HTTPException(status_code=404, detail="Fishing spot not found or not owned by you")

    await db.delete(spot)
    await db.commit()


# ============== Meeting Points ==============


@router.get("/fishing-spots/{spot_id}/meeting-points")
async def list_meeting_points(
    spot_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List meeting points for a fishing spot (owned by user or global)."""
    # Verify spot access
    spot_query = select(FishingSpot).where(
        FishingSpot.id == spot_id,
        or_(
            FishingSpot.owner_id == current_user.id,
            FishingSpot.owner_id.is_(None)
        )
    )
    spot_result = await db.execute(spot_query)
    spot = spot_result.scalar_one_or_none()

    if not spot:
        raise HTTPException(status_code=404, detail="Fishing spot not found")

    # Get meeting points (user's + global for this spot)
    query = select(MeetingPoint).where(
        MeetingPoint.fishing_spot_id == spot_id,
        or_(
            MeetingPoint.owner_id == current_user.id,
            MeetingPoint.owner_id.is_(None)
        )
    ).order_by(MeetingPoint.name)

    result = await db.execute(query)
    points = result.scalars().all()

    return [
        {
            "id": p.id,
            "fishing_spot_id": p.fishing_spot_id,
            "owner_id": p.owner_id,
            "name": p.name,
            "address": p.address,
            "latitude": p.latitude,
            "longitude": p.longitude,
            "description": p.description,
            "is_mine": p.owner_id == current_user.id,
        }
        for p in points
    ]


@router.post("/fishing-spots/{spot_id}/meeting-points", response_model=MeetingPointResponse, status_code=status.HTTP_201_CREATED)
async def create_meeting_point(
    spot_id: int,
    data: MeetingPointCreate,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a meeting point for a fishing spot."""
    # Verify spot access
    spot_query = select(FishingSpot).where(
        FishingSpot.id == spot_id,
        or_(
            FishingSpot.owner_id == current_user.id,
            FishingSpot.owner_id.is_(None)
        )
    )
    spot_result = await db.execute(spot_query)
    spot = spot_result.scalar_one_or_none()

    if not spot:
        raise HTTPException(status_code=404, detail="Fishing spot not found")

    point = MeetingPoint(
        fishing_spot_id=spot_id,
        owner_id=current_user.id,
        name=data.name,
        address=data.address,
        latitude=data.latitude,
        longitude=data.longitude,
        description=data.description,
    )
    db.add(point)
    await db.commit()
    await db.refresh(point)

    return MeetingPointResponse(
        id=point.id,
        fishing_spot_id=point.fishing_spot_id,
        owner_id=point.owner_id,
        name=point.name,
        address=point.address,
        latitude=point.latitude,
        longitude=point.longitude,
        description=point.description,
        is_mine=True,
    )


@router.get("/meeting-points/{point_id}", response_model=MeetingPointResponse)
async def get_meeting_point(
    point_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a meeting point by ID."""
    query = select(MeetingPoint).where(
        MeetingPoint.id == point_id,
        or_(
            MeetingPoint.owner_id == current_user.id,
            MeetingPoint.owner_id.is_(None)
        )
    )
    result = await db.execute(query)
    point = result.scalar_one_or_none()

    if not point:
        raise HTTPException(status_code=404, detail="Meeting point not found")

    return MeetingPointResponse(
        id=point.id,
        fishing_spot_id=point.fishing_spot_id,
        owner_id=point.owner_id,
        name=point.name,
        address=point.address,
        latitude=point.latitude,
        longitude=point.longitude,
        description=point.description,
        is_mine=point.owner_id == current_user.id,
    )


@router.patch("/meeting-points/{point_id}", response_model=MeetingPointResponse)
async def update_meeting_point(
    point_id: int,
    data: MeetingPointUpdate,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a meeting point (must be owned by user)."""
    query = select(MeetingPoint).where(
        MeetingPoint.id == point_id,
        MeetingPoint.owner_id == current_user.id
    )
    result = await db.execute(query)
    point = result.scalar_one_or_none()

    if not point:
        raise HTTPException(status_code=404, detail="Meeting point not found or not owned by you")

    # Update fields
    update_dict = data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        setattr(point, field, value)

    await db.commit()
    await db.refresh(point)

    return MeetingPointResponse(
        id=point.id,
        fishing_spot_id=point.fishing_spot_id,
        owner_id=point.owner_id,
        name=point.name,
        address=point.address,
        latitude=point.latitude,
        longitude=point.longitude,
        description=point.description,
        is_mine=True,
    )


@router.delete("/meeting-points/{point_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meeting_point(
    point_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a meeting point (must be owned by user)."""
    query = select(MeetingPoint).where(
        MeetingPoint.id == point_id,
        MeetingPoint.owner_id == current_user.id
    )
    result = await db.execute(query)
    point = result.scalar_one_or_none()

    if not point:
        raise HTTPException(status_code=404, detail="Meeting point not found or not owned by you")

    await db.delete(point)
    await db.commit()
