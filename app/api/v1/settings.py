"""Public settings endpoints for frontend dropdown options."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.settings import VideoDurationOption

router = APIRouter()


@router.get("/video-durations")
async def list_video_durations(
    db: AsyncSession = Depends(get_db),
):
    """List all active video duration options for event creation dropdown."""
    query = (
        select(VideoDurationOption)
        .where(VideoDurationOption.is_active == True)
        .order_by(VideoDurationOption.display_order, VideoDurationOption.seconds)
    )
    result = await db.execute(query)
    options = result.scalars().all()

    # Get max value for default
    max_seconds = max((o.seconds for o in options), default=5)

    return {
        "options": [
            {
                "id": o.id,
                "seconds": o.seconds,
                "label": o.label,
            }
            for o in options
        ],
        "default_seconds": max_seconds,
    }
