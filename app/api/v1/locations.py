"""Location endpoints (countries, cities, fishing spots) with cascade support."""

from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.location import Country, City, FishingSpot

router = APIRouter()


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
