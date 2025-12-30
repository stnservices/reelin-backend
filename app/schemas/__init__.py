"""Pydantic schemas for request/response validation."""

from app.schemas.user import (
    UserCreate,
    UserLogin,
    UserResponse,
    UserProfileResponse,
    UserProfileUpdate,
    TokenResponse,
    TokenRefresh,
)
from app.schemas.event import (
    EventCreate,
    EventUpdate,
    EventResponse,
    EventListResponse,
    EventTypeResponse,
    ScoringConfigResponse,
)
from app.schemas.common import PaginatedResponse, MessageResponse
from app.schemas.achievement import (
    AchievementDefinitionResponse,
    UserAchievementResponse,
    AchievementProgressResponse,
    EventTypeStatsResponse,
    UserStatisticsResponse,
    UserAchievementsListResponse,
    AchievementGalleryResponse,
    AchievementUnlockNotification,
)

# Trout Area schemas
from app.schemas.trout_area import (
    TAPointsRuleResponse,
    TAEventSettingsResponse,
    TALineupResponse,
    TAMatchResponse,
    TAGameCardResponse,
    TAQualifierStandingResponse,
    TADurationEstimateResponse,
)

# Trout Shore Fishing schemas
from app.schemas.trout_shore import (
    TSFEventSettingsResponse,
    TSFDayResponse,
    TSFLegResponse,
    TSFLineupResponse,
    TSFLegPositionResponse,
    TSFDayStandingResponse,
    TSFFinalStandingResponse,
)

__all__ = [
    # User
    "UserCreate",
    "UserLogin",
    "UserResponse",
    "UserProfileResponse",
    "UserProfileUpdate",
    "TokenResponse",
    "TokenRefresh",
    # Event
    "EventCreate",
    "EventUpdate",
    "EventResponse",
    "EventListResponse",
    "EventTypeResponse",
    "ScoringConfigResponse",
    # Common
    "PaginatedResponse",
    "MessageResponse",
    # Achievement
    "AchievementDefinitionResponse",
    "UserAchievementResponse",
    "AchievementProgressResponse",
    "EventTypeStatsResponse",
    "UserStatisticsResponse",
    "UserAchievementsListResponse",
    "AchievementGalleryResponse",
    "AchievementUnlockNotification",
    # Trout Area
    "TAPointsRuleResponse",
    "TAEventSettingsResponse",
    "TALineupResponse",
    "TAMatchResponse",
    "TAGameCardResponse",
    "TAQualifierStandingResponse",
    "TADurationEstimateResponse",
    # Trout Shore Fishing
    "TSFEventSettingsResponse",
    "TSFDayResponse",
    "TSFLegResponse",
    "TSFLineupResponse",
    "TSFLegPositionResponse",
    "TSFDayStandingResponse",
    "TSFFinalStandingResponse",
]
