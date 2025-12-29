"""Fish species endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.fish import Fish

router = APIRouter()


@router.get("")
async def list_fish(
    db: AsyncSession = Depends(get_db),
):
    """List all active fish species."""
    query = select(Fish).where(Fish.is_active == True).order_by(Fish.name)
    result = await db.execute(query)
    fish = result.scalars().all()
    return [
        {
            "id": f.id,
            "slug": f.slug,
            "name": f.name,
            "name_en": f.name_en,
            "name_ro": f.name_ro,
            "scientific_name": f.scientific_name,
            "min_length": f.min_length,
            "max_length": f.max_length,
            "image_url": f.image_url,
        }
        for f in fish
    ]
