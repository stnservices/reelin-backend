"""User account and profile models."""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserAccount(Base):
    """User authentication account."""

    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # Nullable for social-only accounts
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # Profile picture from OAuth
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Pro subscription fields
    is_pro: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    pro_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pro_stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True, index=True)
    pro_stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True, index=True)
    pro_plan_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 'monthly', 'yearly'
    pro_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Ban fields
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    banned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ban_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    normalized_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    # Account deletion - grace period support
    deletion_scheduled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )  # When user requested deletion (NULL = not scheduled)

    # Relationships
    profile: Mapped["UserProfile"] = relationship(
        "UserProfile", back_populates="user", uselist=False, lazy="joined"
    )
    social_accounts: Mapped[List["SocialAccount"]] = relationship(
        "SocialAccount", back_populates="user", lazy="selectin"
    )
    notification_preferences: Mapped[Optional["UserNotificationPreferences"]] = relationship(
        "UserNotificationPreferences", back_populates="user", uselist=False, lazy="noload"
    )
    device_tokens: Mapped[List["UserDeviceToken"]] = relationship(
        "UserDeviceToken", back_populates="user", lazy="noload"
    )
    minigame_scores: Mapped[List["MinigameScore"]] = relationship(
        "MinigameScore", back_populates="user", lazy="noload"
    )

    @property
    def has_password(self) -> bool:
        """Check if user has a password set (not social-only account)."""
        return self.password_hash is not None

    @property
    def effective_avatar_url(self) -> Optional[str]:
        """Get the user's avatar URL, preferring profile picture over OAuth avatar."""
        if self.profile and self.profile.profile_picture_url:
            return self.profile.profile_picture_url
        return self.avatar_url

    def __repr__(self) -> str:
        return f"<UserAccount(id={self.id}, email={self.email})>"


class UserProfile(Base):
    """User profile with personal information and roles."""

    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # male, female, other, prefer_not_to_say
    profile_picture_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    profile_picture_status: Mapped[str] = mapped_column(
        String(20), default="approved", server_default="approved", nullable=False
    )  # pending, approved, rejected

    # Roles stored as JSONB array: ["angler", "organizer", "validator", "administrator", "sponsor"]
    roles: Mapped[List[str]] = mapped_column(JSONB, default=list, nullable=False)

    # Social links (PRO feature)
    facebook_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    instagram_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    tiktok_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    youtube_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Privacy settings
    is_profile_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Location
    country_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("countries.id", ondelete="SET NULL"), nullable=True
    )
    city_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("cities.id", ondelete="SET NULL"), nullable=True
    )

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship("UserAccount", back_populates="profile")
    country: Mapped[Optional["Country"]] = relationship("Country", lazy="joined")
    city: Mapped[Optional["City"]] = relationship("City", lazy="joined")

    @property
    def full_name(self) -> str:
        """Get user's full name."""
        return f"{self.first_name} {self.last_name}"

    @property
    def is_angler(self) -> bool:
        """Check if user has angler role."""
        return "angler" in (self.roles or [])

    @property
    def is_organizer(self) -> bool:
        """Check if user has organizer role."""
        return "organizer" in (self.roles or [])

    @property
    def is_validator(self) -> bool:
        """Check if user has validator role."""
        return "validator" in (self.roles or [])

    @property
    def is_administrator(self) -> bool:
        """Check if user has administrator role."""
        return "administrator" in (self.roles or [])

    @property
    def is_sponsor(self) -> bool:
        """Check if user has sponsor role."""
        return "sponsor" in (self.roles or [])

    def has_role(self, role: str) -> bool:
        """Check if user has a specific role."""
        return role in (self.roles or [])

    def has_any_role(self, *roles: str) -> bool:
        """Check if user has any of the specified roles."""
        user_roles = set(self.roles or [])
        return bool(user_roles.intersection(set(roles)))

    def __repr__(self) -> str:
        return f"<UserProfile(id={self.id}, name={self.full_name})>"


class TokenBlacklist(Base):
    """Blacklisted JWT tokens (for logout and token rotation)."""

    __tablename__ = "token_blacklist"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    token_jti: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False
    )
    token_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "access" or "refresh"
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    blacklisted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<TokenBlacklist(id={self.id}, jti={self.token_jti})>"


# Import for type hints (avoid circular imports)
from app.models.location import Country, City
from app.models.social_account import SocialAccount
from app.models.notification import UserNotificationPreferences, UserDeviceToken
from app.models.minigame import MinigameScore
