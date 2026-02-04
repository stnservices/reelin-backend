"""Achievement schemas for request/response validation."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AchievementDefinitionResponse(BaseModel):
    """Achievement definition response."""

    id: int
    code: str
    name: str
    description: str
    category: str  # tiered or special
    achievement_type: str
    tier: Optional[str] = None  # bronze/silver/gold/platinum
    threshold: Optional[int] = None
    event_type_id: Optional[int] = None
    event_type_name: Optional[str] = None
    applicable_formats: Optional[list[str]] = None
    fish_id: Optional[int] = None
    fish_name: Optional[str] = None
    fish_name_ro: Optional[str] = None
    icon_url: Optional[str] = None
    badge_color: Optional[str] = None
    sort_order: int

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_model(cls, achievement) -> "AchievementDefinitionResponse":
        """Create response from model."""
        return cls(
            id=achievement.id,
            code=achievement.code,
            name=achievement.name,
            description=achievement.description,
            category=achievement.category,
            achievement_type=achievement.achievement_type,
            tier=achievement.tier,
            threshold=achievement.threshold,
            event_type_id=achievement.event_type_id,
            event_type_name=achievement.event_type.name if achievement.event_type else None,
            applicable_formats=achievement.applicable_formats,
            fish_id=achievement.fish_id,
            fish_name=achievement.fish.name_en if achievement.fish else None,
            fish_name_ro=achievement.fish.name_ro if achievement.fish else None,
            icon_url=achievement.icon_url,
            badge_color=achievement.badge_color,
            sort_order=achievement.sort_order,
        )


class UserAchievementResponse(BaseModel):
    """User achievement response - earned achievement."""

    id: int
    achievement: AchievementDefinitionResponse
    earned_at: datetime
    event_id: Optional[int] = None
    event_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_model(cls, user_achievement) -> "UserAchievementResponse":
        """Create response from model."""
        return cls(
            id=user_achievement.id,
            achievement=AchievementDefinitionResponse.from_model(user_achievement.achievement),
            earned_at=user_achievement.earned_at,
            event_id=user_achievement.event_id,
            event_name=user_achievement.event.name if user_achievement.event else None,
        )


class AchievementProgressResponse(BaseModel):
    """Progress toward a tiered achievement."""

    achievement_type: str
    achievement_type_display: str  # Human readable name
    current_tier: Optional[str] = None  # Current earned tier (null if none)
    current_tier_name: Optional[str] = None
    current_value: int
    next_tier: Optional[str] = None  # Next tier to earn (null if platinum)
    next_tier_name: Optional[str] = None
    next_threshold: Optional[int] = None
    progress_percentage: float  # 0-100
    fish_id: Optional[int] = None  # For fish-specific progress
    fish_name: Optional[str] = None
    fish_name_ro: Optional[str] = None


class EventTypeStatsResponse(BaseModel):
    """Statistics for a specific event type (or overall)."""

    event_type_id: Optional[int] = None  # null = overall
    event_type_name: str  # "Overall" for null

    # Participation
    total_events: int
    total_events_this_year: int

    # Catches
    total_catches: int
    total_approved_catches: int
    total_rejected_catches: int

    # Rankings
    total_wins: int
    podium_finishes: int
    best_rank: Optional[int] = None

    # Points
    total_points: float
    total_bonus_points: int
    total_penalty_points: int

    # Catch quality
    largest_catch_cm: Optional[float] = None
    largest_catch_species: Optional[str] = None
    largest_catch_species_ro: Optional[str] = None
    average_catch_length: float

    # Species diversity
    unique_species_count: int

    # Streaks
    consecutive_events: int
    max_consecutive_events: int

    # Last activity
    last_event_date: Optional[datetime] = None

    # TA-specific stats (nullable - only for TA participants)
    ta_total_matches: Optional[int] = None
    ta_match_wins: Optional[int] = None
    ta_match_losses: Optional[int] = None
    ta_match_ties: Optional[int] = None
    ta_total_catches: Optional[int] = None
    ta_tournament_wins: Optional[int] = None
    ta_tournament_podiums: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_model(cls, stats) -> "EventTypeStatsResponse":
        """Create response from model."""
        return cls(
            event_type_id=stats.event_type_id,
            event_type_name=stats.event_type.name if stats.event_type else "Overall",
            total_events=stats.total_events,
            total_events_this_year=stats.total_events_this_year,
            total_catches=stats.total_catches,
            total_approved_catches=stats.total_approved_catches,
            total_rejected_catches=stats.total_rejected_catches,
            total_wins=stats.total_wins,
            podium_finishes=stats.podium_finishes,
            best_rank=stats.best_rank,
            total_points=stats.total_points,
            total_bonus_points=stats.total_bonus_points,
            total_penalty_points=stats.total_penalty_points,
            largest_catch_cm=stats.largest_catch_cm,
            largest_catch_species=stats.largest_catch_species.name if stats.largest_catch_species else None,
            largest_catch_species_ro=stats.largest_catch_species.name_ro if stats.largest_catch_species else None,
            average_catch_length=stats.average_catch_length,
            unique_species_count=stats.unique_species_count,
            consecutive_events=stats.consecutive_events,
            max_consecutive_events=stats.max_consecutive_events,
            last_event_date=stats.last_event_date,
            # TA-specific stats
            ta_total_matches=stats.ta_total_matches,
            ta_match_wins=stats.ta_match_wins,
            ta_match_losses=stats.ta_match_losses,
            ta_match_ties=stats.ta_match_ties,
            ta_total_catches=stats.ta_total_catches,
            ta_tournament_wins=stats.ta_tournament_wins,
            ta_tournament_podiums=stats.ta_tournament_podiums,
        )


class UserStatisticsResponse(BaseModel):
    """Complete user statistics with overall and per event type."""

    overall: EventTypeStatsResponse
    by_event_type: list[EventTypeStatsResponse]


class UserAchievementsListResponse(BaseModel):
    """Complete achievements list for a user."""

    earned_achievements: list[UserAchievementResponse]
    progress: list[AchievementProgressResponse]
    total_earned: int
    total_available: int


class AchievementGalleryResponse(BaseModel):
    """All available achievements (badge gallery)."""

    tiered: list[AchievementDefinitionResponse]
    special: list[AchievementDefinitionResponse]
    total: int


class AchievementUnlockNotification(BaseModel):
    """Notification payload when achievement is unlocked."""

    achievement: AchievementDefinitionResponse
    earned_at: datetime
    event_id: Optional[int] = None
    event_name: Optional[str] = None
    message: str  # e.g. "Congratulations! You earned First Blood badge!"
