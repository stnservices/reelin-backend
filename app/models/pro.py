"""Pro subscription models: ProGrant, ProAuditLog, ProSettings.

This module defines the Pro subscription management system for
manual grants, audit logging, and configurable settings.
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import UserAccount


class GrantType(str, Enum):
    """Type of manual Pro grant."""

    MANUAL = "manual"  # General manual grant
    COMPENSATION = "compensation"  # Downtime or issue compensation
    INFLUENCER = "influencer"  # Influencer/partner grant
    TESTER = "tester"  # Beta tester grant
    SUPPORT = "support"  # Customer support grant


class ProAction(str, Enum):
    """Pro audit log action types."""

    GRANT = "grant"  # Manual Pro grant
    REVOKE = "revoke"  # Revoke manual grant
    EXTEND = "extend"  # Extend subscription
    CANCEL = "cancel"  # Cancel subscription
    REFUND = "refund"  # Refund payment
    SUBSCRIPTION_CREATED = "subscription_created"  # Stripe subscription created
    SUBSCRIPTION_UPDATED = "subscription_updated"  # Stripe subscription updated
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"  # Stripe subscription cancelled
    PAYMENT_SUCCEEDED = "payment_succeeded"  # Stripe payment succeeded
    PAYMENT_FAILED = "payment_failed"  # Stripe payment failed


class ProGrant(Base):
    """
    Manual Pro grants for users.

    Used for granting Pro status to users outside of Stripe subscriptions,
    e.g., for influencers, testers, or as compensation for issues.
    """

    __tablename__ = "pro_grants"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # User receiving the grant
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Admin who granted
    granted_by: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"),
        nullable=False,
    )

    # Grant details
    grant_type: Mapped[str] = mapped_column(
        String(30),
        default=GrantType.MANUAL.value,
        nullable=False,
    )
    duration_days: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )  # NULL for lifetime
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True  # NULL for lifetime
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    # Revocation info
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoke_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[user_id], lazy="joined"
    )
    granter: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[granted_by], lazy="joined"
    )
    revoker: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[revoked_by], lazy="joined"
    )

    @property
    def is_lifetime(self) -> bool:
        """Check if this is a lifetime grant."""
        return self.duration_days is None and self.expires_at is None

    @property
    def is_expired(self) -> bool:
        """Check if the grant has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(self.expires_at.tzinfo) > self.expires_at

    def __repr__(self) -> str:
        return f"<ProGrant(id={self.id}, user_id={self.user_id}, type={self.grant_type}, active={self.is_active})>"


class ProAuditLog(Base):
    """
    Audit log for Pro-related admin actions.

    Tracks all admin actions related to Pro subscriptions and grants
    for compliance and troubleshooting purposes.
    """

    __tablename__ = "pro_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Admin who performed the action
    admin_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"),
        nullable=False,
        index=True,
    )

    # User affected by the action
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Action details
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Stripe reference (for Stripe-related actions)
    stripe_event_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Relationships
    admin: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[admin_id], lazy="joined"
    )
    user: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[user_id], lazy="joined"
    )

    def __repr__(self) -> str:
        return f"<ProAuditLog(id={self.id}, action={self.action}, admin={self.admin_id}, user={self.user_id})>"


class SubscriptionStatus(str, Enum):
    """Stripe subscription status."""

    ACTIVE = "active"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    PAST_DUE = "past_due"
    PAUSED = "paused"
    TRIALING = "trialing"
    UNPAID = "unpaid"


class PlanType(str, Enum):
    """Pro subscription plan types."""

    MONTHLY = "monthly"
    YEARLY = "yearly"


class ProSubscription(Base):
    """
    Stripe subscription tracking for Pro users.

    Tracks active and historical Stripe subscriptions linked to users.
    """

    __tablename__ = "pro_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # User who owns the subscription
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Stripe identifiers
    stripe_subscription_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    stripe_customer_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    stripe_price_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Subscription details
    plan_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'monthly' or 'yearly'
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # Stripe status

    # Period tracking
    current_period_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Cancellation tracking
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    canceled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[user_id], lazy="joined"
    )

    @property
    def is_active(self) -> bool:
        """Check if subscription is in active state."""
        return self.status in [SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value]

    def __repr__(self) -> str:
        return f"<ProSubscription(id={self.id}, user_id={self.user_id}, status={self.status})>"


class ProSettings(Base):
    """
    Configurable Pro subscription settings.

    Key-value store for Pro-related settings that can be updated by admins.
    """

    __tablename__ = "pro_settings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Who last updated this setting
    updated_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    updater: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[updated_by], lazy="joined"
    )

    def __repr__(self) -> str:
        return f"<ProSettings(key={self.key}, value={self.value})>"
