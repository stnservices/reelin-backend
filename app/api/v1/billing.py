"""Organizer billing API endpoints.

These endpoints allow organizers to view their billing profile,
pricing rates, and invoices.
"""

from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount
from app.models.billing import (
    OrganizerBillingProfile,
    PricingTier,
    PlatformInvoice,
    InvoiceStatus,
)
from app.schemas.billing import (
    BillingProfileCreate,
    BillingProfileUpdate,
    BillingProfileResponse,
    PricingTierResponse,
    PricingTierListResponse,
    InvoiceListItem,
    InvoiceDetailResponse,
    InvoiceListResponse,
    BillingSummary,
)
from app.services.stripe_billing import stripe_billing_service

router = APIRouter()


# ============== Helper Functions ==============


def billing_profile_to_response(
    profile: OrganizerBillingProfile,
) -> BillingProfileResponse:
    """Convert billing profile model to response schema."""
    return BillingProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        organizer_type=profile.organizer_type,
        legal_name=profile.legal_name,
        tax_id=profile.tax_id,
        registration_number=profile.registration_number,
        billing_address_line1=profile.billing_address_line1,
        billing_address_line2=profile.billing_address_line2,
        billing_city=profile.billing_city,
        billing_county=profile.billing_county,
        billing_postal_code=profile.billing_postal_code,
        billing_country=profile.billing_country,
        billing_email=profile.billing_email,
        billing_phone=profile.billing_phone,
        stripe_customer_id=profile.stripe_customer_id,
        is_vat_payer=profile.is_vat_payer,
        vat_rate=profile.vat_rate,
        is_verified=profile.is_verified,
        is_active=profile.is_active,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def pricing_tier_to_response(tier: PricingTier) -> PricingTierResponse:
    """Convert pricing tier model to response schema."""
    return PricingTierResponse(
        id=tier.id,
        billing_profile_id=tier.billing_profile_id,
        event_type_id=tier.event_type_id,
        event_type_name=tier.event_type.name if tier.event_type else None,
        event_type_code=tier.event_type.code if tier.event_type else None,
        pricing_model=tier.pricing_model,
        rate=tier.rate,
        currency_id=tier.currency_id,
        currency_code=tier.currency.code if tier.currency else None,
        currency_symbol=tier.currency.symbol if tier.currency else None,
        minimum_charge=tier.minimum_charge,
        effective_from=tier.effective_from,
        effective_until=tier.effective_until,
        is_active=tier.is_active,
        created_at=tier.created_at,
    )


def invoice_to_list_item(invoice: PlatformInvoice) -> InvoiceListItem:
    """Convert invoice model to list item schema."""
    return InvoiceListItem(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        event_id=invoice.event_id,
        event_name=invoice.event.name if invoice.event else None,
        total_amount=invoice.total_amount,
        currency_code=invoice.currency_code,
        status=invoice.status,
        issued_at=invoice.issued_at,
        due_date=invoice.due_date,
        paid_at=invoice.paid_at,
    )


def invoice_to_detail(invoice: PlatformInvoice) -> InvoiceDetailResponse:
    """Convert invoice model to detail response schema."""
    return InvoiceDetailResponse(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        billing_profile_id=invoice.billing_profile_id,
        event_id=invoice.event_id,
        event_name=invoice.event.name if invoice.event else None,
        pricing_model_snapshot=invoice.pricing_model_snapshot,
        rate_snapshot=invoice.rate_snapshot,
        participant_count=invoice.participant_count,
        subtotal=invoice.subtotal,
        discount_amount=invoice.discount_amount,
        adjustment_amount=invoice.adjustment_amount,
        adjustment_reason=invoice.adjustment_reason,
        total_amount=invoice.total_amount,
        currency_code=invoice.currency_code,
        status=invoice.status,
        stripe_invoice_id=invoice.stripe_invoice_id,
        stripe_invoice_url=invoice.stripe_invoice_url,
        stripe_pdf_url=invoice.stripe_pdf_url,
        issued_at=invoice.issued_at,
        due_date=invoice.due_date,
        paid_at=invoice.paid_at,
        cancelled_at=invoice.cancelled_at,
        line_items=invoice.line_items if invoice.line_items else [],
        notes=invoice.notes,
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
        organizer_name=invoice.billing_profile.legal_name if invoice.billing_profile else None,
        organizer_email=invoice.billing_profile.billing_email if invoice.billing_profile else None,
    )


# ============== Billing Profile Endpoints ==============


@router.get("/profile", response_model=Optional[BillingProfileResponse])
async def get_my_billing_profile(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get the current user's billing profile.
    Returns null if no billing profile exists.
    """
    query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.user_id == current_user.id
    )
    result = await db.execute(query)
    profile = result.scalar_one_or_none()

    if not profile:
        return None

    return billing_profile_to_response(profile)


@router.put("/profile", response_model=BillingProfileResponse)
async def update_my_billing_profile(
    profile_data: BillingProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Update the current user's billing profile.
    Only updates provided fields.
    """
    query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.user_id == current_user.id
    )
    result = await db.execute(query)
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No billing profile found. Contact admin to create one.",
        )

    # Update only provided fields
    update_data = profile_data.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in update_data.items():
        # Don't allow updating notes (admin only)
        if field != "notes":
            setattr(profile, field, value)

    # Update Stripe customer if we have one
    if profile.stripe_customer_id:
        await stripe_billing_service.update_customer(
            profile.stripe_customer_id, profile
        )

    await db.commit()
    await db.refresh(profile)

    return billing_profile_to_response(profile)


# ============== Pricing Endpoints ==============


@router.get("/pricing", response_model=PricingTierListResponse)
async def get_my_pricing(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get current pricing rates for the organizer by event type.
    Only shows active (current) pricing tiers.
    """
    # First get the billing profile
    profile_query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.user_id == current_user.id
    )
    profile_result = await db.execute(profile_query)
    profile = profile_result.scalar_one_or_none()

    if not profile:
        return PricingTierListResponse(items=[], total=0)

    # Get active pricing tiers
    query = (
        select(PricingTier)
        .options(
            selectinload(PricingTier.event_type),
            selectinload(PricingTier.currency),
        )
        .where(
            PricingTier.billing_profile_id == profile.id,
            PricingTier.effective_until.is_(None),  # Only active tiers
        )
        .order_by(PricingTier.event_type_id)
    )

    result = await db.execute(query)
    tiers = result.scalars().all()

    return PricingTierListResponse(
        items=[pricing_tier_to_response(tier) for tier in tiers],
        total=len(tiers),
    )


# ============== Invoice Endpoints ==============


@router.get("/invoices", response_model=InvoiceListResponse)
async def list_my_invoices(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    List invoices for the current user.
    Supports filtering by status and pagination.
    """
    # First get the billing profile
    profile_query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.user_id == current_user.id
    )
    profile_result = await db.execute(profile_query)
    profile = profile_result.scalar_one_or_none()

    if not profile:
        return InvoiceListResponse(
            items=[], total=0, page=page, per_page=per_page, pages=0
        )

    # Build query
    query = (
        select(PlatformInvoice)
        .options(selectinload(PlatformInvoice.event))
        .where(PlatformInvoice.billing_profile_id == profile.id)
    )

    if status_filter:
        query = query.where(PlatformInvoice.status == status_filter)

    # Count total
    count_query = select(func.count(PlatformInvoice.id)).where(
        PlatformInvoice.billing_profile_id == profile.id
    )
    if status_filter:
        count_query = count_query.where(PlatformInvoice.status == status_filter)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    query = (
        query.order_by(PlatformInvoice.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )

    result = await db.execute(query)
    invoices = result.scalars().all()

    return InvoiceListResponse(
        items=[invoice_to_list_item(inv) for inv in invoices],
        total=total,
        page=page,
        per_page=per_page,
        pages=ceil(total / per_page) if total > 0 else 0,
    )


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetailResponse)
async def get_my_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get details of a specific invoice.
    Only returns invoices belonging to the current user.
    """
    # First get the billing profile
    profile_query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.user_id == current_user.id
    )
    profile_result = await db.execute(profile_query)
    profile = profile_result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    # Get the invoice
    query = (
        select(PlatformInvoice)
        .options(
            selectinload(PlatformInvoice.event),
            selectinload(PlatformInvoice.billing_profile),
        )
        .where(
            PlatformInvoice.id == invoice_id,
            PlatformInvoice.billing_profile_id == profile.id,
        )
    )

    result = await db.execute(query)
    invoice = result.scalar_one_or_none()

    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    return invoice_to_detail(invoice)


# ============== Summary Endpoint ==============


@router.get("/summary", response_model=BillingSummary)
async def get_my_billing_summary(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get billing summary for the current user.
    Shows total invoices, pending/paid amounts, and overdue count.
    """
    # First get the billing profile
    profile_query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.user_id == current_user.id
    )
    profile_result = await db.execute(profile_query)
    profile = profile_result.scalar_one_or_none()

    if not profile:
        return BillingSummary(
            total_invoices=0,
            pending_amount=0,
            paid_amount=0,
            overdue_count=0,
            currency_code="EUR",
        )

    # Count total invoices
    total_query = select(func.count(PlatformInvoice.id)).where(
        PlatformInvoice.billing_profile_id == profile.id
    )
    total_result = await db.execute(total_query)
    total_invoices = total_result.scalar() or 0

    # Sum pending amounts
    pending_query = select(func.sum(PlatformInvoice.total_amount)).where(
        PlatformInvoice.billing_profile_id == profile.id,
        PlatformInvoice.status == InvoiceStatus.PENDING.value,
    )
    pending_result = await db.execute(pending_query)
    pending_amount = pending_result.scalar() or 0

    # Sum paid amounts
    paid_query = select(func.sum(PlatformInvoice.total_amount)).where(
        PlatformInvoice.billing_profile_id == profile.id,
        PlatformInvoice.status == InvoiceStatus.PAID.value,
    )
    paid_result = await db.execute(paid_query)
    paid_amount = paid_result.scalar() or 0

    # Count overdue
    overdue_query = select(func.count(PlatformInvoice.id)).where(
        PlatformInvoice.billing_profile_id == profile.id,
        PlatformInvoice.status == InvoiceStatus.OVERDUE.value,
    )
    overdue_result = await db.execute(overdue_query)
    overdue_count = overdue_result.scalar() or 0

    return BillingSummary(
        total_invoices=total_invoices,
        pending_amount=pending_amount,
        paid_amount=paid_amount,
        overdue_count=overdue_count,
        currency_code="EUR",
    )
