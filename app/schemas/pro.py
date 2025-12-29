"""Pydantic schemas for Pro subscription management."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, EmailStr


# ============================================================================
# Enums
# ============================================================================

class GrantTypeEnum(str, Enum):
    """Type of manual Pro grant."""
    MANUAL = "manual"
    COMPENSATION = "compensation"
    INFLUENCER = "influencer"
    TESTER = "tester"
    SUPPORT = "support"


class ProPlanType(str, Enum):
    """Pro subscription plan type."""
    MONTHLY = "monthly"
    YEARLY = "yearly"


class ProStatusFilter(str, Enum):
    """Filter options for user Pro status."""
    ALL = "all"
    PRO_ONLY = "pro_only"
    FREE_ONLY = "free_only"
    MANUAL_GRANTS = "manual_grants"
    STRIPE_SUBSCRIPTIONS = "stripe_subscriptions"


class SubscriptionStatus(str, Enum):
    """Subscription status values."""
    ACTIVE = "active"
    CANCELLED = "cancelled"
    PAST_DUE = "past_due"
    TRIALING = "trialing"
    INCOMPLETE = "incomplete"
    EXPIRED = "expired"


# ============================================================================
# Pro Stats Schemas
# ============================================================================

class ProStatsResponse(BaseModel):
    """Pro subscription statistics."""
    total_pro_users: int
    active_subscriptions: int
    manual_grants: int
    mrr: Decimal = Field(description="Monthly Recurring Revenue in EUR")
    arr: Decimal = Field(description="Annual Recurring Revenue in EUR")
    churn_rate: float = Field(description="Churn rate percentage")
    new_this_month: int
    monthly_subscribers: int
    yearly_subscribers: int
    conversion_rate: float = Field(description="Free to Pro conversion rate percentage")


class RevenueDataPoint(BaseModel):
    """Revenue data point for charts."""
    month: str
    revenue: Decimal
    subscribers: int


class ConversionFunnelData(BaseModel):
    """Conversion funnel data."""
    total_users: int
    free_users: int
    trial_users: int
    pro_users: int


# ============================================================================
# Pro Grant Schemas
# ============================================================================

class ProGrantCreate(BaseModel):
    """Schema for creating a manual Pro grant."""
    user_id: int = Field(..., description="User ID to grant Pro to")
    grant_type: GrantTypeEnum = Field(default=GrantTypeEnum.MANUAL)
    duration_days: Optional[int] = Field(None, ge=1, description="Duration in days (null for lifetime)")
    reason: str = Field(..., min_length=5, max_length=500, description="Reason for grant")


class ProGrantResponse(BaseModel):
    """Schema for Pro grant response."""
    id: int
    user_id: int
    user_email: str
    user_name: str
    granted_by: int
    granter_name: str
    grant_type: str
    duration_days: Optional[int]
    starts_at: datetime
    expires_at: Optional[datetime]
    reason: str
    is_active: bool
    is_lifetime: bool
    revoked_at: Optional[datetime]
    revoked_by: Optional[int]
    revoker_name: Optional[str]
    revoke_reason: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ProGrantRevoke(BaseModel):
    """Schema for revoking a Pro grant."""
    reason: str = Field(..., min_length=5, max_length=500, description="Reason for revocation")


# ============================================================================
# Subscription Schemas
# ============================================================================

class SubscriptionListItem(BaseModel):
    """Schema for subscription list item."""
    id: int
    user_id: int
    user_email: str
    user_name: str
    plan_type: Optional[str]
    status: str
    started_at: Optional[datetime]
    expires_at: Optional[datetime]
    amount: Optional[Decimal]
    stripe_subscription_id: Optional[str]
    stripe_customer_id: Optional[str]
    is_manual_grant: bool = False
    grant_id: Optional[int] = None

    class Config:
        from_attributes = True


class SubscriptionDetail(BaseModel):
    """Detailed subscription information."""
    id: int
    user_id: int
    user_email: str
    user_name: str
    plan_type: Optional[str]
    status: str
    started_at: Optional[datetime]
    expires_at: Optional[datetime]
    amount: Optional[Decimal]
    stripe_subscription_id: Optional[str]
    stripe_customer_id: Optional[str]
    stripe_portal_url: Optional[str]
    is_manual_grant: bool = False
    manual_grants: list[ProGrantResponse] = []
    payment_history: list[dict] = []

    class Config:
        from_attributes = True


class SubscriptionExtend(BaseModel):
    """Schema for extending a subscription."""
    days: int = Field(..., ge=1, le=365, description="Number of days to add")
    reason: str = Field(..., min_length=5, max_length=500, description="Reason for extension")


class SubscriptionCancel(BaseModel):
    """Schema for canceling a subscription."""
    immediate: bool = Field(default=False, description="Cancel immediately vs at end of period")
    reason: str = Field(..., min_length=5, max_length=500, description="Reason for cancellation")


class RefundCreate(BaseModel):
    """Schema for creating a refund."""
    amount: Optional[Decimal] = Field(None, description="Amount to refund (null for full refund)")
    reason: str = Field(..., min_length=5, max_length=500, description="Reason for refund")


# ============================================================================
# User Pro Status Schemas
# ============================================================================

class UserProStatus(BaseModel):
    """User with Pro status information."""
    id: int
    email: str
    first_name: str
    last_name: str
    full_name: str
    is_pro: bool
    pro_source: Optional[str] = None  # 'stripe', 'manual_grant', None
    plan_type: Optional[str]
    expires_at: Optional[datetime]
    started_at: Optional[datetime]
    stripe_subscription_id: Optional[str]
    active_grant_id: Optional[int]

    class Config:
        from_attributes = True


# ============================================================================
# Audit Log Schemas
# ============================================================================

class AuditLogEntry(BaseModel):
    """Schema for audit log entry."""
    id: int
    admin_id: int
    admin_email: str
    admin_name: str
    user_id: int
    user_email: str
    user_name: str
    action: str
    details: Optional[dict]
    reason: Optional[str]
    stripe_event_id: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogFilter(BaseModel):
    """Filter for audit log queries."""
    user_id: Optional[int] = None
    admin_id: Optional[int] = None
    action: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


# ============================================================================
# Settings Schemas
# ============================================================================

class ProSettingResponse(BaseModel):
    """Pro setting response."""
    id: int
    key: str
    value: str
    description: Optional[str]
    updated_by: Optional[int]
    updater_name: Optional[str]
    updated_at: datetime

    class Config:
        from_attributes = True


class ProSettingUpdate(BaseModel):
    """Schema for updating a Pro setting."""
    value: str = Field(..., min_length=1, max_length=100)


class ProSettingsResponse(BaseModel):
    """All Pro settings response."""
    trial_duration_days: int
    grace_period_days: int
    monthly_price_eur: Decimal
    yearly_price_eur: Decimal


# ============================================================================
# Pagination
# ============================================================================

class PaginatedResponse(BaseModel):
    """Generic paginated response."""
    items: list[Any]
    total: int
    page: int
    page_size: int
    total_pages: int


class SubscriptionListResponse(PaginatedResponse):
    """Paginated subscription list response."""
    items: list[SubscriptionListItem]


class UserProStatusListResponse(PaginatedResponse):
    """Paginated user Pro status list response."""
    items: list[UserProStatus]


class AuditLogListResponse(PaginatedResponse):
    """Paginated audit log list response."""
    items: list[AuditLogEntry]


class ProGrantListResponse(PaginatedResponse):
    """Paginated Pro grant list response."""
    items: list[ProGrantResponse]
