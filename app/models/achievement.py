"""Achievement models: AchievementDefinition, UserAchievement, UserAchievementProgress."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AchievementCategory(str, Enum):
    """Achievement category enum."""

    TIERED = "tiered"      # Bronze -> Silver -> Gold -> Platinum progression
    SPECIAL = "special"    # One-time unique badges


class AchievementTier(str, Enum):
    """Achievement tier enum for tiered badges."""

    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"
    NONE = "none"  # For special badges


class AchievementType(str, Enum):
    """Achievement type enum - what triggers the achievement."""

    # Tiered achievement types
    PARTICIPATION = "participation"      # Number of events participated
    CATCH_COUNT = "catch_count"          # Total approved catches
    SPECIES_COUNT = "species_count"      # Unique species caught
    PODIUM_COUNT = "podium_count"        # Number of podium finishes (top 3)
    WIN_COUNT = "win_count"              # Number of first place finishes
    FISH_CATCH_COUNT = "fish_catch_count"  # Catches of a specific fish species
    PREDATOR_CATCH_COUNT = "predator_catch_count"  # Total predator fish catches

    # Special achievement types
    FIRST_CATCH = "first_catch"          # First catch ever validated
    EARLY_BIRD = "early_bird"            # First catch within 30 min of event start
    LAST_MINUTE = "last_minute"          # Catch in final 30 minutes
    SPEED_DEMON = "speed_demon"          # 5 catches in first hour
    TROPHY_HUNTER = "trophy_hunter"      # Catch a fish >= 50cm
    MONSTER_CATCH = "monster_catch"      # Set a new personal best length
    PRECISION_ANGLER = "precision_angler"  # 90%+ catches above min length in single event
    HOT_STREAK = "hot_streak"            # 3 podium finishes in a row
    DOMINATOR = "dominator"              # 2 wins in a row
    IRON_MAN = "iron_man"                # 5 consecutive events participated
    CLEAN_SHEET = "clean_sheet"          # Event with no rejected catches
    COMEBACK_KING = "comeback_king"      # Improve 5+ ranks from initial position
    DIVERSITY_MASTER = "diversity_master"  # Catch every available species in single event


class AchievementDefinition(Base):
    """
    Achievement definition model.
    Defines all available achievements in the system.
    Seeded via migration with predefined achievements.
    """

    __tablename__ = "achievement_definitions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Category and type
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # tiered or special
    achievement_type: Mapped[str] = mapped_column(String(50), nullable=False)  # what triggers it

    # Tier information (for tiered achievements)
    tier: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # bronze/silver/gold/platinum
    threshold: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # value needed to earn

    # Event type specificity (null = applies to all event types)
    event_type_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("event_types.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Fish species specificity (for species-specific achievements like "Pike Master")
    fish_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("fish.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Display
    icon_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    badge_color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # hex color
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    event_type: Mapped[Optional["EventType"]] = relationship("EventType", lazy="joined")
    fish: Mapped[Optional["Fish"]] = relationship("Fish", lazy="joined")
    user_achievements: Mapped[list["UserAchievement"]] = relationship(
        "UserAchievement", back_populates="achievement", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<AchievementDefinition(id={self.id}, code={self.code}, tier={self.tier})>"


class UserAchievement(Base):
    """
    User achievement model.
    Records when a user earns an achievement.
    """

    __tablename__ = "user_achievements"
    __table_args__ = (
        UniqueConstraint("user_id", "achievement_id", name="uq_user_achievement"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    achievement_id: Mapped[int] = mapped_column(
        ForeignKey("achievement_definitions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # When earned
    earned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Context (optional - which event/catch triggered it)
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    catch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("catches.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")
    achievement: Mapped["AchievementDefinition"] = relationship(
        "AchievementDefinition", back_populates="user_achievements", lazy="joined"
    )
    event: Mapped[Optional["Event"]] = relationship("Event", lazy="joined")
    catch: Mapped[Optional["Catch"]] = relationship("Catch", lazy="joined")

    def __repr__(self) -> str:
        return f"<UserAchievement(id={self.id}, user_id={self.user_id}, achievement_id={self.achievement_id})>"


class UserAchievementProgress(Base):
    """
    User achievement progress model.
    Tracks progress toward tiered achievements.
    Allows filtering by event type.
    """

    __tablename__ = "user_achievement_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "achievement_type", "event_type_id", name="uq_user_achievement_progress"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # What type of achievement this tracks progress for
    achievement_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Event type specificity (null = overall across all event types)
    event_type_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("event_types.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Current progress value
    current_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Timestamps
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")
    event_type: Mapped[Optional["EventType"]] = relationship("EventType", lazy="joined")

    def __repr__(self) -> str:
        return f"<UserAchievementProgress(id={self.id}, user_id={self.user_id}, type={self.achievement_type}, value={self.current_value})>"


class UserStreakTracker(Base):
    """
    User streak tracker model.
    Tracks consecutive achievements like wins, podiums, and participation.
    """

    __tablename__ = "user_streak_trackers"
    __table_args__ = (
        UniqueConstraint("user_id", "streak_type", name="uq_user_streak_tracker"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Type of streak being tracked
    streak_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # podium, win, participation

    # Current and max streak values
    current_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Last event that updated this streak
    last_event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )

    # Timestamps
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")
    last_event: Mapped[Optional["Event"]] = relationship("Event", lazy="joined")

    def __repr__(self) -> str:
        return f"<UserStreakTracker(id={self.id}, user_id={self.user_id}, type={self.streak_type}, current={self.current_streak})>"


# Import for type hints (avoid circular imports)
from app.models.user import UserAccount
from app.models.event import Event, EventType
from app.models.catch import Catch
from app.models.fish import Fish
