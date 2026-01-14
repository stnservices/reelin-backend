"""Public Hall of Fame endpoints for mobile app."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import get_db
from app.models.hall_of_fame import HallOfFameEntry
from app.schemas.hall_of_fame import (
    HallOfFamePublicResponse,
    HallOfFameGroupedResponse,
)

router = APIRouter()


@router.get("", response_model=HallOfFameGroupedResponse)
async def get_hall_of_fame(
    db: AsyncSession = Depends(get_db),
    format_code: Optional[str] = Query(None, pattern="^(sf|ta)$"),
) -> HallOfFameGroupedResponse:
    """
    Get all Hall of Fame entries grouped by achievement type.

    Public endpoint for mobile app.
    """
    query = select(HallOfFameEntry).options(joinedload(HallOfFameEntry.user))

    if format_code:
        query = query.where(HallOfFameEntry.format_code == format_code)

    query = query.order_by(
        HallOfFameEntry.competition_year.desc(),
        HallOfFameEntry.position
    )

    result = await db.execute(query)
    entries = list(result.scalars().unique().all())

    # Group by achievement type
    grouped = HallOfFameGroupedResponse()

    for entry in entries:
        response = HallOfFamePublicResponse(
            id=entry.id,
            user_id=entry.user_id,
            athlete_name=entry.athlete_name,
            display_name=entry.display_name,
            avatar_url=entry.avatar_url,
            achievement_type=entry.achievement_type,
            competition_name=entry.competition_name,
            competition_year=entry.competition_year,
            position=entry.position,
            format_code=entry.format_code,
            category=entry.category,
            country=entry.country,
            image_url=entry.image_url,
        )

        if entry.achievement_type == "world_champion":
            grouped.world_champions.append(response)
        elif entry.achievement_type == "national_champion":
            grouped.national_champions.append(response)
        elif entry.achievement_type == "world_podium":
            grouped.world_podiums.append(response)
        elif entry.achievement_type == "national_podium":
            grouped.national_podiums.append(response)

    return grouped


@router.get("/world-champions", response_model=List[HallOfFamePublicResponse])
async def get_world_champions(
    db: AsyncSession = Depends(get_db),
    format_code: Optional[str] = Query(None, pattern="^(sf|ta)$"),
    year: Optional[int] = Query(None),
) -> List[HallOfFamePublicResponse]:
    """
    Get world champions.

    Public endpoint for mobile app.
    """
    query = (
        select(HallOfFameEntry)
        .options(joinedload(HallOfFameEntry.user))
        .where(HallOfFameEntry.achievement_type == "world_champion")
    )

    if format_code:
        query = query.where(HallOfFameEntry.format_code == format_code)
    if year:
        query = query.where(HallOfFameEntry.competition_year == year)

    query = query.order_by(
        HallOfFameEntry.competition_year.desc(),
        HallOfFameEntry.position
    )

    result = await db.execute(query)
    entries = list(result.scalars().unique().all())

    return [
        HallOfFamePublicResponse(
            id=entry.id,
            user_id=entry.user_id,
            athlete_name=entry.athlete_name,
            display_name=entry.display_name,
            avatar_url=entry.avatar_url,
            achievement_type=entry.achievement_type,
            competition_name=entry.competition_name,
            competition_year=entry.competition_year,
            position=entry.position,
            format_code=entry.format_code,
            category=entry.category,
            country=entry.country,
            image_url=entry.image_url,
        )
        for entry in entries
    ]


@router.get("/national-champions", response_model=List[HallOfFamePublicResponse])
async def get_national_champions(
    db: AsyncSession = Depends(get_db),
    format_code: Optional[str] = Query(None, pattern="^(sf|ta)$"),
    year: Optional[int] = Query(None),
) -> List[HallOfFamePublicResponse]:
    """
    Get national champions.

    Public endpoint for mobile app.
    """
    query = (
        select(HallOfFameEntry)
        .options(joinedload(HallOfFameEntry.user))
        .where(HallOfFameEntry.achievement_type == "national_champion")
    )

    if format_code:
        query = query.where(HallOfFameEntry.format_code == format_code)
    if year:
        query = query.where(HallOfFameEntry.competition_year == year)

    query = query.order_by(
        HallOfFameEntry.competition_year.desc(),
        HallOfFameEntry.position
    )

    result = await db.execute(query)
    entries = list(result.scalars().unique().all())

    return [
        HallOfFamePublicResponse(
            id=entry.id,
            user_id=entry.user_id,
            athlete_name=entry.athlete_name,
            display_name=entry.display_name,
            avatar_url=entry.avatar_url,
            achievement_type=entry.achievement_type,
            competition_name=entry.competition_name,
            competition_year=entry.competition_year,
            position=entry.position,
            format_code=entry.format_code,
            category=entry.category,
            country=entry.country,
            image_url=entry.image_url,
        )
        for entry in entries
    ]
