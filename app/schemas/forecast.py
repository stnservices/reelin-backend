"""Fishing Forecast Schemas."""

from typing import Optional

from pydantic import BaseModel, Field


class TimePeriod(BaseModel):
    """Solunar feeding period."""

    start: str = Field(..., description="Start time (HH:MM)")
    end: str = Field(..., description="End time (HH:MM)")
    type: str = Field(..., description="Period type: 'major' or 'minor'")


class HourlyForecast(BaseModel):
    """Hourly forecast data."""

    hour: int = Field(..., ge=0, le=23)
    score: int = Field(..., ge=0, le=100)
    temperature: float
    is_major_period: bool = False
    is_minor_period: bool = False


class DailyForecast(BaseModel):
    """Daily forecast summary."""

    date: str
    day_name: str
    day_rating: int = Field(..., ge=0, le=4)
    best_score: int = Field(..., ge=0, le=100)
    best_time: str
    moon_phase: str
    major_periods: list[TimePeriod] = []


class ForecastResponse(BaseModel):
    """Full fishing forecast response."""

    # Current conditions
    current_score: int = Field(..., ge=0, le=100, description="Fishing score 0-100")
    current_label: str = Field(..., description="Score label: Excellent, Good, Fair, Poor, Bad")

    # Solunar data
    sun_rise: Optional[str] = None
    sun_set: Optional[str] = None
    moon_rise: Optional[str] = None
    moon_set: Optional[str] = None
    moon_phase: Optional[str] = None
    moon_illumination: Optional[str] = None
    day_rating: Optional[int] = Field(None, ge=0, le=4, description="Solunar day rating 0-4")

    # Feeding periods (always included)
    major_periods: list[TimePeriod] = Field(default_factory=list, description="Major feeding periods (peak activity)")
    minor_periods: list[TimePeriod] = Field(default_factory=list, description="Minor feeding periods (Pro only detail)")

    # Weather data
    temperature: Optional[float] = Field(None, description="Temperature in Celsius")
    feels_like: Optional[float] = None
    humidity: Optional[int] = None
    pressure: Optional[float] = Field(None, description="Barometric pressure in hPa")
    pressure_trend: Optional[str] = Field(None, description="rising, falling, or steady")
    wind_speed: Optional[float] = Field(None, description="Wind speed in m/s")
    wind_direction: Optional[str] = Field(None, description="Wind compass direction")
    clouds: Optional[int] = Field(None, description="Cloud cover percentage")
    weather_description: Optional[str] = None

    # Pro-only fields
    hourly_forecast: Optional[list[HourlyForecast]] = Field(
        None, description="24-hour breakdown (Pro only)"
    )
    daily_forecast: Optional[list[DailyForecast]] = Field(
        None, description="5-day forecast (Pro only)"
    )

    # Error handling
    error: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "current_score": 78,
                "current_label": "Good",
                "sun_rise": "07:52",
                "sun_set": "16:47",
                "moon_rise": "04:23",
                "moon_set": "14:12",
                "moon_phase": "Waning Crescent",
                "moon_illumination": "15%",
                "day_rating": 3,
                "major_periods": [
                    {"start": "09:30", "end": "11:30", "type": "major"},
                    {"start": "22:00", "end": "00:00", "type": "major"},
                ],
                "minor_periods": [
                    {"start": "03:30", "end": "04:30", "type": "minor"},
                    {"start": "15:45", "end": "16:45", "type": "minor"},
                ],
                "temperature": 15.5,
                "feels_like": 13.2,
                "humidity": 65,
                "pressure": 1013,
                "pressure_trend": "falling",
                "wind_speed": 3.5,
                "wind_direction": "S",
                "clouds": 40,
                "weather_description": "scattered clouds",
            }
        }
