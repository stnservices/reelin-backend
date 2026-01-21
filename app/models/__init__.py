"""SQLAlchemy models for ReelIn application."""

from app.models.user import UserAccount, UserProfile, TokenBlacklist
from app.models.social_account import SocialAccount, OAuthProvider
from app.models.event import Event, EventType, ScoringConfig, EventPrize, EventScoringRule, EventFishScoring, EventSpeciesBonusPoints
from app.models.event_validator import EventValidator
from app.models.enrollment import EventEnrollment
from app.models.catch import Catch, EventScoreboard, RankingMovement
from app.models.club import Club, ClubMembership
from app.models.location import Country, City, FishingSpot, MeetingPoint
from app.models.fish import Fish
from app.models.notification import Notification, UserNotificationPreferences, UserDeviceToken, DeviceType, CatchNotificationLevel
from app.models.sponsor import Sponsor
from app.models.event_sponsor import EventSponsor
from app.models.admin import AdminActionLog, AdminActionType
from app.models.team import Team, TeamMember, TeamMemberRole
from app.models.rules import OrganizerRule, OrganizerRuleDefault
from app.models.contestation import EventContestation, ContestationStatus, ContestationType
from app.models.organizer_message import OrganizerMessage
from app.models.admin_message import AdminMessage
from app.models.event_chat import EventChatMessage, MessageType
from app.models.currency import Currency
from app.models.settings import VideoDurationOption
from app.models.pro import (
    ProGrant, ProAuditLog, ProSettings, ProSubscription,
    GrantType, ProAction, SubscriptionStatus, PlanType
)
from app.models.billing import (
    OrganizerBillingProfile,
    PricingTier,
    PlatformInvoice,
    OrganizerType,
    PricingModel,
    InvoiceStatus,
)
from app.models.achievement import (
    AchievementDefinition,
    UserAchievement,
    UserAchievementProgress,
    UserStreakTracker,
    AchievementCategory,
    AchievementTier,
    AchievementType,
)
from app.models.statistics import UserEventTypeStats
from app.models.follow import UserFollow
from app.models.waypoint import UserWaypoint, WaypointIcon, WaypointCategory
from app.models.recommendation import RecommendationDismissal
from app.models.app_settings import AppSettings
from app.models.ai_analysis import CatchAiAnalysis, AiAnalysisStatus
from app.models.profile_moderation import ProfilePictureModeration, ModerationStatus, RejectionReason
from app.models.ml_model import MLModel, MLPredictionLog
from app.models.organizer_permissions import OrganizerEventTypeAccess, NationalEventOrganizer
from app.models.partner import Partner
from app.models.news import News
from app.models.hall_of_fame import HallOfFameEntry

# Minigame
from app.models.minigame import MinigameScore

# Trout Area (TA) models
from app.models.trout_area import (
    TAPointsRule,
    TAEventSettings,
    TALineup,
    TAGameCard,
    TAMatch,
    TAKnockoutBracket,
    TAKnockoutMatch,
    TAQualifierStanding,
    TAMatchOutcome,
    TATournamentPhase,
    TAMatchStatus,
    TAGameCardStatus,
)


__all__ = [
    # User
    "UserAccount",
    "UserProfile",
    "TokenBlacklist",
    # Social Auth
    "SocialAccount",
    "OAuthProvider",
    # Event
    "Event",
    "EventType",
    "ScoringConfig",
    "EventPrize",
    "EventScoringRule",
    "EventFishScoring",
    "EventSpeciesBonusPoints",
    # Event Validators
    "EventValidator",
    # Enrollment
    "EventEnrollment",
    # Catch & Scoring
    "Catch",
    "EventScoreboard",
    "RankingMovement",
    # Club
    "Club",
    "ClubMembership",
    # Location
    "Country",
    "City",
    "FishingSpot",
    "MeetingPoint",
    # Fish
    "Fish",
    # Notification
    "Notification",
    "UserNotificationPreferences",
    "UserDeviceToken",
    "DeviceType",
    "CatchNotificationLevel",
    # Sponsor
    "Sponsor",
    "EventSponsor",
    # Admin
    "AdminActionLog",
    "AdminActionType",
    # Team
    "Team",
    "TeamMember",
    "TeamMemberRole",
    # Rules
    "OrganizerRule",
    "OrganizerRuleDefault",
    # Contestation
    "EventContestation",
    "ContestationStatus",
    "ContestationType",
    # Organizer Messages
    "OrganizerMessage",
    # Admin Messages
    "AdminMessage",
    # Event Chat
    "EventChatMessage",
    "MessageType",
    # Currency
    "Currency",
    # Settings
    "VideoDurationOption",
    # Pro
    "ProGrant",
    "ProAuditLog",
    "ProSettings",
    "ProSubscription",
    "GrantType",
    "ProAction",
    "SubscriptionStatus",
    "PlanType",
    # Billing
    "OrganizerBillingProfile",
    "PricingTier",
    "PlatformInvoice",
    "OrganizerType",
    "PricingModel",
    "InvoiceStatus",
    # Achievement
    "AchievementDefinition",
    "UserAchievement",
    "UserAchievementProgress",
    "UserStreakTracker",
    "AchievementCategory",
    "AchievementTier",
    "AchievementType",
    # Statistics
    "UserEventTypeStats",
    # Follow
    "UserFollow",
    # Waypoints
    "UserWaypoint",
    "WaypointIcon",
    "WaypointCategory",
    # Recommendations
    "RecommendationDismissal",
    # App Settings
    "AppSettings",
    # AI Analysis
    "CatchAiAnalysis",
    "AiAnalysisStatus",
    # Profile Moderation
    "ProfilePictureModeration",
    "ModerationStatus",
    "RejectionReason",
    # ML Models
    "MLModel",
    "MLPredictionLog",
    # Organizer Permissions
    "OrganizerEventTypeAccess",
    "NationalEventOrganizer",
    # Partner
    "Partner",
    # News
    "News",
    # Hall of Fame
    "HallOfFameEntry",
    # Minigame
    "MinigameScore",
    # Trout Area (TA)
    "TAPointsRule",
    "TAEventSettings",
    "TALineup",
    "TAGameCard",
    "TAMatch",
    "TAKnockoutBracket",
    "TAKnockoutMatch",
    "TAQualifierStanding",
    "TAMatchOutcome",
    "TATournamentPhase",
    "TAMatchStatus",
    "TAGameCardStatus",
]
