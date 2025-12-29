"""Personal Analytics Schemas."""

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class CatchSummary(BaseModel):
    """Summary of a single catch."""

    id: int
    species_id: int
    species_name: str
    length_cm: float
    weight_kg: Optional[float] = None
    catch_date: str
    event_name: Optional[str] = None
    photo_url: Optional[str] = None


class PersonalBest(BaseModel):
    """Personal best catch for a species."""

    species_id: int
    species_name: str
    length_cm: float
    weight_kg: Optional[float] = None
    catch_date: str
    event_name: Optional[str] = None
    photo_url: Optional[str] = None


class SpeciesCount(BaseModel):
    """Species breakdown entry."""

    species_id: int
    species_name: str
    count: int
    percentage: float
    average_length: float
    max_length: float


class LocationStat(BaseModel):
    """Top fishing location stats."""

    name: str
    latitude: float
    longitude: float
    catch_count: int
    last_catch_date: Optional[str] = None


class HeatmapPoint(BaseModel):
    """Heatmap data point."""

    latitude: float
    longitude: float
    count: int
    intensity: float = Field(..., ge=0, le=1)


class MonthlyTrend(BaseModel):
    """Monthly trend data."""

    month: str  # "YYYY-MM"
    catch_count: int
    average_length: float
    best_catch_length: float


class RecentCatch(BaseModel):
    """Recent catch for free users."""

    id: int
    species_name: str
    length_cm: float
    catch_date: str
    event_name: Optional[str] = None


class PersonalAnalyticsResponse(BaseModel):
    """Full personal analytics response."""

    # Overview
    total_catches: int
    total_events: int
    total_species: int
    total_length_cm: float
    average_length_cm: float

    # Personal bests
    biggest_catch: Optional[CatchSummary] = None
    personal_bests: list[PersonalBest] = []

    # Species breakdown
    species_counts: list[SpeciesCount] = []

    # Time analysis (Pro only)
    catches_by_hour: Optional[dict[str, int]] = None
    catches_by_day_of_week: Optional[dict[str, int]] = None
    catches_by_month: Optional[dict[str, int]] = None
    best_time_of_day: Optional[str] = None
    best_day_of_week: Optional[str] = None

    # Location insights (Pro only)
    top_locations: Optional[list[LocationStat]] = None
    catch_heatmap: Optional[list[HeatmapPoint]] = None

    # Trends (Pro only)
    monthly_trend: Optional[list[MonthlyTrend]] = None
    improvement_rate: Optional[float] = Field(None, description="% change vs previous period")

    # Free users only
    last_catches: Optional[list[RecentCatch]] = None

    class Config:
        json_schema_extra = {
            "example": {
                "total_catches": 127,
                "total_events": 23,
                "total_species": 12,
                "total_length_cm": 5840.5,
                "average_length_cm": 46.0,
                "biggest_catch": {
                    "id": 456,
                    "species_id": 1,
                    "species_name": "Carp",
                    "length_cm": 78.0,
                    "catch_date": "2025-12-15",
                    "event_name": "Winter Challenge",
                },
                "personal_bests": [
                    {
                        "species_id": 1,
                        "species_name": "Carp",
                        "length_cm": 78.0,
                        "catch_date": "2025-12-15",
                    }
                ],
                "species_counts": [
                    {
                        "species_id": 1,
                        "species_name": "Carp",
                        "count": 45,
                        "percentage": 35.5,
                        "average_length": 42.3,
                        "max_length": 78.0,
                    }
                ],
            }
        }
