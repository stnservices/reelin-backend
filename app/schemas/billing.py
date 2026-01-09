"""Pydantic schemas for billing endpoints."""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ============== Billing Profile Schemas ==============


class BillingProfileBrief(BaseModel):
    """Brief billing profile info for event responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    legal_name: str
    organizer_type: str


class BillingProfileCreate(BaseModel):
    """Schema for creating a billing profile."""

    organizer_type: str = Field(
        default="association", pattern="^(association|company|individual)$"
    )
    legal_name: str = Field(..., min_length=1, max_length=255)
    cnp: Optional[str] = Field(
        None,
        min_length=13,
        max_length=13,
        pattern=r"^[1-8]\d{12}$",
        description="CNP (Cod Numeric Personal) - 13 digits, required for individual organizer type"
    )
    tax_id: Optional[str] = Field(None, max_length=50)
    registration_number: Optional[str] = Field(None, max_length=100)
    billing_address_line1: str = Field(..., min_length=1, max_length=255)
    billing_address_line2: Optional[str] = Field(None, max_length=255)
    billing_city: str = Field(..., min_length=1, max_length=100)
    billing_county: Optional[str] = Field(None, max_length=100)
    billing_postal_code: str = Field(..., min_length=1, max_length=20)
    billing_country: str = Field(default="RO", min_length=2, max_length=2)
    billing_email: EmailStr
    billing_phone: Optional[str] = Field(None, max_length=30)
    is_vat_payer: bool = Field(default=False)
    vat_rate: Optional[Decimal] = Field(None, ge=0, le=100)


class BillingProfileUpdate(BaseModel):
    """Schema for updating a billing profile."""

    organizer_type: Optional[str] = Field(
        None, pattern="^(association|company|individual)$"
    )
    legal_name: Optional[str] = Field(None, min_length=1, max_length=255)
    cnp: Optional[str] = Field(
        None,
        min_length=13,
        max_length=13,
        pattern=r"^[1-8]\d{12}$",
        description="CNP (Cod Numeric Personal) - 13 digits, for individual organizer type"
    )
    tax_id: Optional[str] = Field(None, max_length=50)
    registration_number: Optional[str] = Field(None, max_length=100)
    is_primary: Optional[bool] = Field(
        None,
        description="Set to true to make this the primary billing profile (clears other profiles' is_primary)"
    )
    billing_address_line1: Optional[str] = Field(None, min_length=1, max_length=255)
    billing_address_line2: Optional[str] = None
    billing_city: Optional[str] = Field(None, min_length=1, max_length=100)
    billing_county: Optional[str] = None
    billing_postal_code: Optional[str] = Field(None, min_length=1, max_length=20)
    billing_country: Optional[str] = Field(None, min_length=2, max_length=2)
    billing_email: Optional[EmailStr] = None
    billing_phone: Optional[str] = None
    is_vat_payer: Optional[bool] = None
    vat_rate: Optional[Decimal] = Field(None, ge=0, le=100)
    notes: Optional[str] = None


class BillingProfileResponse(BaseModel):
    """Schema for billing profile response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    is_primary: bool = False
    organizer_type: str
    legal_name: str
    cnp: Optional[str] = None
    tax_id: Optional[str] = None
    registration_number: Optional[str] = None
    billing_address_line1: str
    billing_address_line2: Optional[str] = None
    billing_city: str
    billing_county: Optional[str] = None
    billing_postal_code: str
    billing_country: str
    billing_email: str
    billing_phone: Optional[str] = None
    stripe_customer_id: Optional[str] = None
    is_vat_payer: bool = False
    vat_rate: Optional[Decimal] = None
    is_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class BillingProfileListItem(BaseModel):
    """Schema for billing profile list item (admin view)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    is_primary: bool = False
    organizer_type: str
    legal_name: str
    cnp: Optional[str] = None
    billing_email: str
    is_verified: bool
    is_active: bool
    created_at: datetime

    # User info (from relationship)
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    invoice_count: int = 0


class BillingProfileListResponse(BaseModel):
    """Schema for paginated billing profile list response."""

    items: List[BillingProfileListItem]
    total: int
    page: int
    per_page: int


# ============== Pricing Tier Schemas ==============


class PricingTierCreate(BaseModel):
    """Schema for creating a pricing tier."""

    event_type_id: int
    pricing_model: str = Field(
        default="per_participant", pattern="^(per_participant|fixed)$"
    )
    rate: Decimal = Field(..., ge=0, decimal_places=2)
    currency_id: int
    minimum_charge: Optional[Decimal] = Field(None, ge=0, decimal_places=2)


class PricingTierUpdate(BaseModel):
    """Schema for updating a pricing tier (creates new version)."""

    pricing_model: Optional[str] = Field(
        None, pattern="^(per_participant|fixed)$"
    )
    rate: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    currency_id: Optional[int] = None
    minimum_charge: Optional[Decimal] = None


class PricingTierResponse(BaseModel):
    """Schema for pricing tier response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    billing_profile_id: int
    event_type_id: int
    event_type_name: Optional[str] = None
    event_type_code: Optional[str] = None
    pricing_model: str
    rate: Decimal
    currency_id: int
    currency_code: Optional[str] = None
    currency_symbol: Optional[str] = None
    minimum_charge: Optional[Decimal] = None
    effective_from: datetime
    effective_until: Optional[datetime] = None
    is_active: bool
    created_at: datetime


class PricingTierListResponse(BaseModel):
    """Schema for pricing tier list response."""

    items: List[PricingTierResponse]
    total: int


# ============== Invoice Schemas ==============


class InvoiceLineItem(BaseModel):
    """Schema for invoice line item."""

    description: str
    quantity: int
    unit_price: Decimal
    amount: Decimal


class InvoiceListItem(BaseModel):
    """Schema for invoice list item."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str
    event_id: int
    event_name: Optional[str] = None
    total_amount: Decimal
    currency_code: str
    status: str
    issued_at: Optional[datetime] = None
    due_date: Optional[datetime] = None
    paid_at: Optional[datetime] = None


class InvoiceDetailResponse(BaseModel):
    """Schema for invoice detail."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str
    billing_profile_id: int
    event_id: int
    event_name: Optional[str] = None
    pricing_model_snapshot: str
    rate_snapshot: Decimal
    participant_count: int
    subtotal: Decimal
    discount_amount: Decimal
    adjustment_amount: Decimal
    adjustment_reason: Optional[str] = None
    total_amount: Decimal
    currency_code: str
    status: str
    stripe_invoice_id: Optional[str] = None
    stripe_invoice_url: Optional[str] = None
    stripe_pdf_url: Optional[str] = None
    issued_at: Optional[datetime] = None
    due_date: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    line_items: List[dict] = []
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # Billing profile info
    organizer_name: Optional[str] = None
    organizer_email: Optional[str] = None


class InvoiceListResponse(BaseModel):
    """Schema for paginated invoice list response."""

    items: List[InvoiceListItem]
    total: int
    page: int
    per_page: int
    pages: int


class InvoiceAdjustment(BaseModel):
    """Schema for applying invoice adjustment."""

    adjustment_amount: Decimal
    adjustment_reason: str = Field(..., min_length=1, max_length=500)


class InvoiceFilter(BaseModel):
    """Schema for invoice filtering."""

    status: Optional[str] = None
    billing_profile_id: Optional[int] = None
    event_id: Optional[int] = None
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None


# ============== Summary Schemas ==============


class BillingSummary(BaseModel):
    """Schema for organizer billing summary."""

    total_invoices: int
    pending_amount: Decimal
    paid_amount: Decimal
    overdue_count: int
    currency_code: str = "EUR"


class AdminBillingSummary(BaseModel):
    """Schema for admin billing summary."""

    total_invoices: int
    total_pending: Decimal
    total_paid: Decimal
    total_overdue: int
    profiles_count: int
    verified_profiles: int


# ============== Event Type Default Billing Profile Schemas ==============


class EventTypeDefaultUpdate(BaseModel):
    """Schema for setting default billing profile for an event type."""

    billing_profile_id: Optional[int] = Field(
        None, description="Billing profile ID to set as default. Set to null to clear."
    )


class EventTypeDefaultResponse(BaseModel):
    """Schema for event type with default billing profile info."""

    model_config = ConfigDict(from_attributes=True)

    event_type_id: int
    event_type_name: str
    event_type_code: str
    default_billing_profile_id: Optional[int] = None
    default_billing_profile_name: Optional[str] = None
    granted_at: datetime


class EventTypeDefaultsListResponse(BaseModel):
    """Schema for list of event type default billing profile assignments."""

    items: List[EventTypeDefaultResponse]
    total: int
