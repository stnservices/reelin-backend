"""Fishing Forecast API endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models import UserAccount
from app.models.forecast import ForecastQuery
from app.schemas.forecast import ForecastResponse
from app.services.forecast_service import forecast_service
from app.api.v1.pro import is_user_pro

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/forecast", tags=["Forecast"])


@router.get("", response_model=ForecastResponse)
async def get_fishing_forecast(
    lat: float = Query(..., ge=-90, le=90, description="Latitude"),
    lng: float = Query(..., ge=-180, le=180, description="Longitude"),
    days: int = Query(1, ge=1, le=5, description="Forecast days (Pro: up to 5)"),
    timezone: int = Query(2, ge=-12, le=14, description="Timezone offset"),
    current_user: Optional[UserAccount] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Get fishing forecast for a location.

    Combines solunar data (moon phases, feeding periods) with weather data
    to calculate a fishing score from 0-100.

    **Free users:** Current score + major periods only
    **Pro users:** Full hourly breakdown + 5-day forecast

    The fishing score considers:
    - Solunar periods (major/minor feeding times)
    - Moon phase (new/full moon = better fishing)
    - Barometric pressure (falling = fish feed more)
    - Wind speed (light breeze = better casting)
    - Cloud cover (overcast often better)
    - Temperature extremes
    """
    # Check if user is Pro (using proper check that looks at subscriptions/grants)
    user_is_pro = False
    if current_user:
        user_is_pro = await is_user_pro(current_user.id, db)

    # Limit features for non-Pro users
    actual_days = days if user_is_pro else 1
    include_hourly = user_is_pro

    forecast = await forecast_service.get_forecast(
        lat=lat,
        lng=lng,
        days=actual_days,
        include_hourly=include_hourly,
        timezone=timezone,
    )

    # Remove minor periods detail for free users (they can see major only)
    if not user_is_pro:
        # Keep major periods, clear minor periods for free users
        forecast["minor_periods"] = []

    try:
        query_log = ForecastQuery(
            user_id=current_user.id if current_user else None,
            latitude=lat,
            longitude=lng,
            timezone=timezone,
            days=actual_days,
            score=forecast.get("current_score"),
        )
        db.add(query_log)
        await db.commit()
    except Exception:
        logger.exception("Failed to log forecast query")
        await db.rollback()

    return ForecastResponse(**forecast)


@router.get("/score", response_model=dict)
async def get_simple_score(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    db: AsyncSession = Depends(get_db),
):
    """
    Get just the current fishing score (lightweight endpoint).

    Returns only the score and label, useful for widgets.
    """
    forecast = await forecast_service.get_forecast(
        lat=lat,
        lng=lng,
        days=1,
        include_hourly=False,
    )

    try:
        query_log = ForecastQuery(
            latitude=lat,
            longitude=lng,
            score=forecast.get("current_score"),
        )
        db.add(query_log)
        await db.commit()
    except Exception:
        logger.exception("Failed to log forecast score query")
        await db.rollback()

    return {
        "score": forecast.get("current_score", 50),
        "label": forecast.get("current_label", "Unknown"),
        "moon_phase": forecast.get("moon_phase", ""),
    }
