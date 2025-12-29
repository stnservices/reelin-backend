"""Personal Analytics API endpoints."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount
from app.schemas.analytics import PersonalAnalyticsResponse
from app.services.analytics_service import analytics_service

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/personal", response_model=PersonalAnalyticsResponse)
async def get_personal_analytics(
    period: str = Query("all", pattern="^(all|year|month|week|custom)$"),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get personal catch analytics.

    - **Free users**: Basic stats (total catches, events, species) + last 10 catches
    - **Pro users**: Full analytics including time analysis, heatmap, trends

    Period options:
    - `all`: All time
    - `year`: Current year
    - `month`: Current month
    - `week`: Last 7 days
    - `custom`: Custom date range (requires start_date and end_date)
    """
    if not current_user.is_pro:
        # Return limited data for free users
        data = await analytics_service.get_basic_stats(db, current_user.id)
        return PersonalAnalyticsResponse(**data)

    # Full analytics for Pro users
    data = await analytics_service.get_full_analytics(
        db=db,
        user_id=current_user.id,
        period=period,
        start_date=start_date,
        end_date=end_date,
    )
    return PersonalAnalyticsResponse(**data)
