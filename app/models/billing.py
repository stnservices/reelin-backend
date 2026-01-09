"""Billing models: OrganizerBillingProfile, PricingTier, PlatformInvoice.

This module defines the platform billing system for invoicing organizers
for their completed events.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.currency import Currency
    from app.models.event import Event, EventType
    from app.models.user import UserAccount


class OrganizerType(str, Enum):
    """Organizer legal entity type."""

    ASSOCIATION = "association"  # Non-profit organization (usually VAT exempt)
    COMPANY = "company"  # For-profit company (SRL, SA, etc.) - usually VAT payer
    INDIVIDUAL = "individual"  # Individual person (PFA, CNP)


class PricingModel(str, Enum):
    """Pricing model for billing organizers."""

    PER_PARTICIPANT = "per_participant"  # rate × approved participant count
    FIXED = "fixed"  # flat rate regardless of participants


class InvoiceStatus(str, Enum):
    """Platform invoice status."""

    DRAFT = "draft"  # Created but not sent to Stripe
    PENDING = "pending"  # Sent via Stripe, awaiting payment
    PAID = "paid"  # Paid in full
    OVERDUE = "overdue"  # Past due date
    CANCELLED = "cancelled"  # Cancelled/voided
    REFUNDED = "refunded"  # Payment refunded


class OrganizerBillingProfile(Base):
    """
    Billing profile for organizers.

    One-to-many relationship with UserAccount - organizers can have multiple
    billing profiles representing different legal entities (Associations, Companies, Individuals).
    Stores legal and billing information needed for invoicing via Stripe.
    Each billing profile creates its own Stripe customer.
    """

    __tablename__ = "organizer_billing_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Link to user (organizer) - not unique, allows multiple profiles per user
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Primary profile designation - only one per user can be primary
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )  # First profile is auto-set to True

    # Legal entity type
    organizer_type: Mapped[str] = mapped_column(
        String(20),
        default=OrganizerType.ASSOCIATION.value,
        nullable=False,
    )

    # Legal information
    legal_name: Mapped[str] = mapped_column(
        String(255), nullable=False
    )  # Official registered name
    cnp: Mapped[Optional[str]] = mapped_column(
        String(13), nullable=True
    )  # Cod Numeric Personal (13 digits) - for individual organizer type only
    tax_id: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # CUI/CIF for companies/associations (not used for individuals)
    registration_number: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # J/number for associations

    # Billing address
    billing_address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    billing_address_line2: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    billing_city: Mapped[str] = mapped_column(String(100), nullable=False)
    billing_county: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # Judet
    billing_postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    billing_country: Mapped[str] = mapped_column(
        String(2), default="RO", nullable=False
    )  # ISO 3166-1 alpha-2

    # Contact for billing
    billing_email: Mapped[str] = mapped_column(String(255), nullable=False)
    billing_phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # Stripe integration
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )

    # VAT handling
    is_vat_payer: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )  # Whether they charge/pay VAT
    vat_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )  # VAT rate percentage (e.g., 19.00 for 19%)

    # Status
    is_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )  # Admin verified billing info
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Notes (for admin)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")
    pricing_tiers: Mapped[List["PricingTier"]] = relationship(
        "PricingTier",
        back_populates="billing_profile",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    invoices: Mapped[List["PlatformInvoice"]] = relationship(
        "PlatformInvoice",
        back_populates="billing_profile",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<OrganizerBillingProfile(id={self.id}, user_id={self.user_id}, legal_name={self.legal_name})>"


class PricingTier(Base):
    """
    Pricing configuration per organizer per event type.

    Supports version history - when rate changes, old record gets an
    effective_until timestamp and new one is created. This ensures
    historical invoices reference the correct rates.
    """

    __tablename__ = "pricing_tiers"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Link to billing profile
    billing_profile_id: Mapped[int] = mapped_column(
        ForeignKey("organizer_billing_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Link to event type
    event_type_id: Mapped[int] = mapped_column(
        ForeignKey("event_types.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Pricing model
    pricing_model: Mapped[str] = mapped_column(
        String(20),
        default=PricingModel.PER_PARTICIPANT.value,
        nullable=False,
    )

    # Rate (interpretation depends on pricing_model)
    # per_participant: amount per approved participant
    # fixed: flat rate for the event
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # Currency - link to currencies table
    currency_id: Mapped[int] = mapped_column(
        ForeignKey("currencies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Minimum charge (optional floor for per_participant model)
    minimum_charge: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )

    # Version control for rate history
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    effective_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True  # NULL = currently active
    )
    superseded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pricing_tiers.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Who set this rate
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    billing_profile: Mapped["OrganizerBillingProfile"] = relationship(
        "OrganizerBillingProfile", back_populates="pricing_tiers"
    )
    event_type: Mapped["EventType"] = relationship("EventType", lazy="joined")
    currency: Mapped["Currency"] = relationship("Currency", lazy="joined")
    created_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[created_by_id], lazy="joined"
    )
    superseded_by: Mapped[Optional["PricingTier"]] = relationship(
        "PricingTier", remote_side=[id], lazy="joined"
    )

    @property
    def is_active(self) -> bool:
        """Check if this tier is currently active."""
        return self.effective_until is None

    def __repr__(self) -> str:
        return f"<PricingTier(id={self.id}, profile={self.billing_profile_id}, event_type={self.event_type_id}, rate={self.rate})>"


class PlatformInvoice(Base):
    """
    Invoice from ReelIn platform to an organizer for an event.

    Generated when an event is completed. Synced with Stripe Invoicing API.
    Stores a snapshot of the pricing at invoice creation time.
    """

    __tablename__ = "platform_invoices"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Invoice identification
    invoice_number: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )

    # Link to billing profile
    billing_profile_id: Mapped[int] = mapped_column(
        ForeignKey("organizer_billing_profiles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Link to event
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Pricing snapshot (captured at invoice creation)
    pricing_tier_id: Mapped[int] = mapped_column(
        ForeignKey("pricing_tiers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    pricing_model_snapshot: Mapped[str] = mapped_column(String(20), nullable=False)
    rate_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # Billing details
    participant_count: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # Approved participants at completion
    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )  # Before any adjustments
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )
    adjustment_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False
    )  # Manual override
    adjustment_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)

    # Status tracking
    status: Mapped[str] = mapped_column(
        String(20),
        default=InvoiceStatus.DRAFT.value,
        nullable=False,
        index=True,
    )

    # Stripe integration
    stripe_invoice_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )
    stripe_invoice_url: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )  # Hosted invoice URL
    stripe_pdf_url: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )  # PDF download URL

    # Important dates
    issued_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    due_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Additional metadata
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Internal notes
    line_items: Mapped[dict] = mapped_column(
        JSONB, default=list, nullable=False
    )  # Detailed breakdown

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    billing_profile: Mapped["OrganizerBillingProfile"] = relationship(
        "OrganizerBillingProfile", back_populates="invoices"
    )
    event: Mapped["Event"] = relationship("Event", lazy="joined")
    pricing_tier: Mapped["PricingTier"] = relationship("PricingTier", lazy="joined")

    def __repr__(self) -> str:
        return f"<PlatformInvoice(id={self.id}, number={self.invoice_number}, event={self.event_id}, status={self.status})>"
