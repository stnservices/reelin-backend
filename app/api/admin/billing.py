"""Admin billing management endpoints.

These endpoints allow administrators to manage organizer billing profiles,
pricing tiers, and invoices.
"""

from datetime import datetime, timezone
from decimal import Decimal
from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.permissions import AdminOnly
from app.models.user import UserAccount, UserProfile
from app.models.billing import (
    OrganizerBillingProfile,
    PricingTier,
    PlatformInvoice,
    InvoiceStatus,
    PricingModel,
)
from app.models.event import Event, EventType
from app.models.currency import Currency
from app.schemas.billing import (
    BillingProfileCreate,
    BillingProfileUpdate,
    BillingProfileResponse,
    BillingProfileListItem,
    BillingProfileListResponse,
    PricingTierCreate,
    PricingTierUpdate,
    PricingTierResponse,
    PricingTierListResponse,
    InvoiceListItem,
    InvoiceDetailResponse,
    InvoiceListResponse,
    InvoiceAdjustment,
    AdminBillingSummary,
)
from app.schemas.common import MessageResponse
from app.services.stripe_billing import stripe_billing_service, validate_romanian_tax_id

router = APIRouter()


# ============== Helper Functions ==============


def profile_to_list_item(
    profile: OrganizerBillingProfile,
    invoice_count: int = 0,
) -> BillingProfileListItem:
    """Convert billing profile to list item schema."""
    user_email = None
    user_name = None
    if profile.user:
        user_email = profile.user.email
        if profile.user.profile:
            user_name = f"{profile.user.profile.first_name} {profile.user.profile.last_name}"

    return BillingProfileListItem(
        id=profile.id,
        user_id=profile.user_id,
        organizer_type=profile.organizer_type,
        legal_name=profile.legal_name,
        billing_email=profile.billing_email,
        is_verified=profile.is_verified,
        is_active=profile.is_active,
        created_at=profile.created_at,
        user_email=user_email,
        user_name=user_name,
        invoice_count=invoice_count,
    )


def profile_to_response(profile: OrganizerBillingProfile) -> BillingProfileResponse:
    """Convert billing profile to response schema."""
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
        is_verified=profile.is_verified,
        is_active=profile.is_active,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def tier_to_response(tier: PricingTier) -> PricingTierResponse:
    """Convert pricing tier to response schema."""
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
    """Convert invoice to list item schema."""
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
    """Convert invoice to detail response schema."""
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


# ============== Billing Profiles ==============


@router.get("/profiles", response_model=BillingProfileListResponse)
async def list_billing_profiles(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    verified_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    List all organizer billing profiles.
    Supports search by legal name or email.
    """
    # Base filter conditions
    base_conditions = [OrganizerBillingProfile.is_active == True]

    if verified_only:
        base_conditions.append(OrganizerBillingProfile.is_verified == True)

    if search:
        search_term = f"%{search}%"
        base_conditions.append(
            or_(
                OrganizerBillingProfile.legal_name.ilike(search_term),
                OrganizerBillingProfile.billing_email.ilike(search_term),
            )
        )

    # Count query
    count_query = select(func.count(OrganizerBillingProfile.id)).where(*base_conditions)
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Main query with pagination
    query = (
        select(OrganizerBillingProfile)
        .options(
            selectinload(OrganizerBillingProfile.user).selectinload(UserAccount.profile)
        )
        .where(*base_conditions)
        .order_by(OrganizerBillingProfile.legal_name)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )

    result = await db.execute(query)
    profiles = result.scalars().all()

    # Get invoice counts for each profile
    profile_ids = [p.id for p in profiles]
    invoice_counts = {}
    if profile_ids:
        invoice_count_query = (
            select(
                PlatformInvoice.billing_profile_id,
                func.count(PlatformInvoice.id).label("count")
            )
            .where(PlatformInvoice.billing_profile_id.in_(profile_ids))
            .group_by(PlatformInvoice.billing_profile_id)
        )
        invoice_result = await db.execute(invoice_count_query)
        for row in invoice_result:
            invoice_counts[row.billing_profile_id] = row.count

    items = [
        profile_to_list_item(p, invoice_counts.get(p.id, 0))
        for p in profiles
    ]

    return BillingProfileListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/profiles/{user_id}", response_model=BillingProfileResponse)
async def get_billing_profile(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Get billing profile for a specific user."""
    query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.user_id == user_id
    )
    result = await db.execute(query)
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing profile not found",
        )

    return profile_to_response(profile)


@router.post(
    "/profiles/{user_id}",
    response_model=BillingProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_billing_profile(
    user_id: int,
    profile_data: BillingProfileCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Create billing profile for an organizer."""
    # Check if user exists and is an organizer
    user_query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == user_id)
    )
    user_result = await db.execute(user_query)
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not user.profile or not user.profile.is_organizer:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not an organizer",
        )

    # Check if profile already exists
    existing_query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.user_id == user_id
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Billing profile already exists for this user",
        )

    # Validate Romanian tax ID if provided
    profile_dict = profile_data.model_dump()
    if profile_dict.get("tax_id") and profile_dict.get("billing_country") == "RO":
        is_valid, cleaned_tax_id = validate_romanian_tax_id(profile_dict["tax_id"])
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid Romanian tax ID format: '{profile_dict['tax_id']}'. Expected 2-10 digits, optionally prefixed with 'RO'.",
            )
        # Use cleaned version
        profile_dict["tax_id"] = cleaned_tax_id

    # Create profile
    profile = OrganizerBillingProfile(
        user_id=user_id,
        **profile_dict,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    return profile_to_response(profile)


@router.put("/profiles/{user_id}", response_model=BillingProfileResponse)
async def update_billing_profile(
    user_id: int,
    profile_data: BillingProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Update billing profile for an organizer."""
    query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.user_id == user_id
    )
    result = await db.execute(query)
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing profile not found",
        )

    # Update fields
    update_data = profile_data.model_dump(exclude_unset=True, exclude_none=True)

    # Validate Romanian tax ID if provided
    tax_id = update_data.get("tax_id")
    billing_country = update_data.get("billing_country", profile.billing_country)
    if tax_id and billing_country == "RO":
        is_valid, cleaned_tax_id = validate_romanian_tax_id(tax_id)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid Romanian tax ID format: '{tax_id}'. Expected 2-10 digits, optionally prefixed with 'RO'.",
            )
        update_data["tax_id"] = cleaned_tax_id

    for field, value in update_data.items():
        setattr(profile, field, value)

    # Update Stripe customer if exists
    if profile.stripe_customer_id:
        await stripe_billing_service.update_customer(
            profile.stripe_customer_id, profile
        )

    await db.commit()
    await db.refresh(profile)

    return profile_to_response(profile)


@router.post("/profiles/{user_id}/verify", response_model=MessageResponse)
async def verify_billing_profile(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Mark billing profile as verified."""
    query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.user_id == user_id
    )
    result = await db.execute(query)
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing profile not found",
        )

    profile.is_verified = True
    await db.commit()

    return MessageResponse(message="Billing profile verified successfully")


# ============== Pricing Tiers ==============


@router.get("/profiles/{user_id}/pricing", response_model=PricingTierListResponse)
async def list_pricing_tiers(
    user_id: int,
    include_history: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    List pricing tiers for an organizer.
    By default only shows active tiers, use include_history=true for all.
    """
    # Get billing profile
    profile_query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.user_id == user_id
    )
    profile_result = await db.execute(profile_query)
    profile = profile_result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing profile not found",
        )

    # Get pricing tiers
    query = (
        select(PricingTier)
        .options(
            selectinload(PricingTier.event_type),
            selectinload(PricingTier.currency),
        )
        .where(PricingTier.billing_profile_id == profile.id)
    )

    if not include_history:
        query = query.where(PricingTier.effective_until.is_(None))

    query = query.order_by(PricingTier.event_type_id, PricingTier.effective_from.desc())

    result = await db.execute(query)
    tiers = result.scalars().all()

    return PricingTierListResponse(
        items=[tier_to_response(t) for t in tiers],
        total=len(tiers),
    )


@router.post(
    "/profiles/{user_id}/pricing",
    response_model=PricingTierResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_pricing_tier(
    user_id: int,
    tier_data: PricingTierCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Create a new pricing tier for an organizer."""
    # Get billing profile
    profile_query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.user_id == user_id
    )
    profile_result = await db.execute(profile_query)
    profile = profile_result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing profile not found",
        )

    # Check if active tier already exists for this event type
    existing_query = select(PricingTier).where(
        PricingTier.billing_profile_id == profile.id,
        PricingTier.event_type_id == tier_data.event_type_id,
        PricingTier.effective_until.is_(None),
    )
    existing_result = await db.execute(existing_query)
    existing = existing_result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active pricing tier already exists for this event type. Use update endpoint.",
        )

    # Create tier
    tier = PricingTier(
        billing_profile_id=profile.id,
        created_by_id=current_user.id,
        **tier_data.model_dump(),
    )
    db.add(tier)
    await db.commit()
    await db.refresh(tier, ["event_type", "currency"])

    return tier_to_response(tier)


@router.put("/pricing/{tier_id}", response_model=PricingTierResponse)
async def update_pricing_tier(
    tier_id: int,
    tier_data: PricingTierUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    Update a pricing tier.
    This creates a new version and supersedes the old one to maintain history.
    """
    # Get current tier
    query = (
        select(PricingTier)
        .options(
            selectinload(PricingTier.event_type),
            selectinload(PricingTier.currency),
        )
        .where(PricingTier.id == tier_id)
    )
    result = await db.execute(query)
    current_tier = result.scalar_one_or_none()

    if not current_tier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pricing tier not found",
        )

    if current_tier.effective_until is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update a superseded pricing tier",
        )

    # Get update data
    update_data = tier_data.model_dump(exclude_unset=True, exclude_none=True)

    # Check if there are actual changes
    has_changes = False
    for field, value in update_data.items():
        if getattr(current_tier, field) != value:
            has_changes = True
            break

    if not has_changes:
        return tier_to_response(current_tier)

    # Create new tier with updated values
    now = datetime.now(timezone.utc)
    new_tier = PricingTier(
        billing_profile_id=current_tier.billing_profile_id,
        event_type_id=current_tier.event_type_id,
        pricing_model=update_data.get("pricing_model", current_tier.pricing_model),
        rate=update_data.get("rate", current_tier.rate),
        currency_code=update_data.get("currency_code", current_tier.currency_code),
        minimum_charge=update_data.get("minimum_charge", current_tier.minimum_charge),
        effective_from=now,
        created_by_id=current_user.id,
    )
    db.add(new_tier)
    await db.flush()  # Get new tier ID

    # Supersede current tier
    current_tier.effective_until = now
    current_tier.superseded_by_id = new_tier.id

    await db.commit()
    await db.refresh(new_tier, ["event_type"])

    return tier_to_response(new_tier)


# ============== Invoices ==============


@router.get("/invoices", response_model=InvoiceListResponse)
async def list_all_invoices(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    billing_profile_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """List all platform invoices with filtering options."""
    query = (
        select(PlatformInvoice)
        .options(selectinload(PlatformInvoice.event))
    )

    if status_filter:
        query = query.where(PlatformInvoice.status == status_filter)
    if billing_profile_id:
        query = query.where(PlatformInvoice.billing_profile_id == billing_profile_id)

    # Count total
    count_query = select(func.count(PlatformInvoice.id))
    if status_filter:
        count_query = count_query.where(PlatformInvoice.status == status_filter)
    if billing_profile_id:
        count_query = count_query.where(
            PlatformInvoice.billing_profile_id == billing_profile_id
        )

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
async def get_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Get invoice details."""
    query = (
        select(PlatformInvoice)
        .options(
            selectinload(PlatformInvoice.event),
            selectinload(PlatformInvoice.billing_profile),
        )
        .where(PlatformInvoice.id == invoice_id)
    )

    result = await db.execute(query)
    invoice = result.scalar_one_or_none()

    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    return invoice_to_detail(invoice)


@router.post("/invoices/{invoice_id}/send", response_model=MessageResponse)
async def send_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Send an invoice via Stripe."""
    query = (
        select(PlatformInvoice)
        .options(selectinload(PlatformInvoice.billing_profile))
        .where(PlatformInvoice.id == invoice_id)
    )
    result = await db.execute(query)
    invoice = result.scalar_one_or_none()

    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    if invoice.status not in [InvoiceStatus.DRAFT.value]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot send invoice with status: {invoice.status}",
        )

    if not invoice.stripe_invoice_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invoice has no Stripe invoice ID",
        )

    success = await stripe_billing_service.send_invoice(invoice.stripe_invoice_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send invoice via Stripe",
        )

    invoice.status = InvoiceStatus.PENDING.value
    invoice.issued_at = datetime.now(timezone.utc)
    await db.commit()

    return MessageResponse(message="Invoice sent successfully")


@router.post("/invoices/{invoice_id}/cancel", response_model=MessageResponse)
async def cancel_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Cancel/void an invoice."""
    query = select(PlatformInvoice).where(PlatformInvoice.id == invoice_id)
    result = await db.execute(query)
    invoice = result.scalar_one_or_none()

    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    if invoice.status in [InvoiceStatus.PAID.value, InvoiceStatus.CANCELLED.value]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel invoice with status: {invoice.status}",
        )

    # Void in Stripe if exists
    if invoice.stripe_invoice_id:
        await stripe_billing_service.void_invoice(invoice.stripe_invoice_id)

    invoice.status = InvoiceStatus.CANCELLED.value
    invoice.cancelled_at = datetime.now(timezone.utc)
    await db.commit()

    return MessageResponse(message="Invoice cancelled successfully")


@router.post("/invoices/{invoice_id}/adjust", response_model=InvoiceDetailResponse)
async def adjust_invoice(
    invoice_id: int,
    adjustment: InvoiceAdjustment,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Apply a manual adjustment to an invoice."""
    query = (
        select(PlatformInvoice)
        .options(
            selectinload(PlatformInvoice.event),
            selectinload(PlatformInvoice.billing_profile),
        )
        .where(PlatformInvoice.id == invoice_id)
    )
    result = await db.execute(query)
    invoice = result.scalar_one_or_none()

    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    if invoice.status not in [InvoiceStatus.DRAFT.value]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only adjust draft invoices",
        )

    invoice.adjustment_amount = adjustment.adjustment_amount
    invoice.adjustment_reason = adjustment.adjustment_reason
    invoice.total_amount = invoice.subtotal - invoice.discount_amount + adjustment.adjustment_amount

    await db.commit()
    await db.refresh(invoice)

    return invoice_to_detail(invoice)


@router.post("/invoices/{invoice_id}/mark-paid", response_model=MessageResponse)
async def mark_invoice_paid(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Manually mark an invoice as paid (for offline payments)."""
    query = select(PlatformInvoice).where(PlatformInvoice.id == invoice_id)
    result = await db.execute(query)
    invoice = result.scalar_one_or_none()

    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    if invoice.status == InvoiceStatus.PAID.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invoice is already paid",
        )

    invoice.status = InvoiceStatus.PAID.value
    invoice.paid_at = datetime.now(timezone.utc)
    await db.commit()

    return MessageResponse(message="Invoice marked as paid")


# ============== Summary ==============


@router.get("/summary", response_model=AdminBillingSummary)
async def get_billing_summary(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Get overall billing summary for admin dashboard."""
    # Count invoices
    total_query = select(func.count(PlatformInvoice.id))
    total_result = await db.execute(total_query)
    total_invoices = total_result.scalar() or 0

    # Sum pending
    pending_query = select(func.sum(PlatformInvoice.total_amount)).where(
        PlatformInvoice.status == InvoiceStatus.PENDING.value
    )
    pending_result = await db.execute(pending_query)
    total_pending = pending_result.scalar() or 0

    # Sum paid
    paid_query = select(func.sum(PlatformInvoice.total_amount)).where(
        PlatformInvoice.status == InvoiceStatus.PAID.value
    )
    paid_result = await db.execute(paid_query)
    total_paid = paid_result.scalar() or 0

    # Count overdue
    overdue_query = select(func.count(PlatformInvoice.id)).where(
        PlatformInvoice.status == InvoiceStatus.OVERDUE.value
    )
    overdue_result = await db.execute(overdue_query)
    total_overdue = overdue_result.scalar() or 0

    # Count profiles
    profiles_query = select(func.count(OrganizerBillingProfile.id)).where(
        OrganizerBillingProfile.is_active == True
    )
    profiles_result = await db.execute(profiles_query)
    profiles_count = profiles_result.scalar() or 0

    # Count verified
    verified_query = select(func.count(OrganizerBillingProfile.id)).where(
        OrganizerBillingProfile.is_active == True,
        OrganizerBillingProfile.is_verified == True,
    )
    verified_result = await db.execute(verified_query)
    verified_profiles = verified_result.scalar() or 0

    return AdminBillingSummary(
        total_invoices=total_invoices,
        total_pending=total_pending,
        total_paid=total_paid,
        total_overdue=total_overdue,
        profiles_count=profiles_count,
        verified_profiles=verified_profiles,
    )


# ============== Reference Data Endpoints ==============


@router.get("/event-types")
async def list_event_types(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """List all event types for pricing tier selection."""
    query = select(EventType).where(EventType.is_active == True).order_by(EventType.name)
    result = await db.execute(query)
    event_types = result.scalars().all()

    return [
        {
            "id": et.id,
            "name": et.name,
            "code": et.code,
        }
        for et in event_types
    ]


@router.get("/currencies")
async def list_currencies(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """List all currencies for pricing tier selection."""
    query = select(Currency).where(Currency.is_active == True).order_by(Currency.code)
    result = await db.execute(query)
    currencies = result.scalars().all()

    return [
        {
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "symbol": c.symbol,
        }
        for c in currencies
    ]


# ============== Event Billing Profile Override ==============


@router.patch("/events/{event_id}/billing-profile")
async def override_event_billing_profile(
    event_id: int,
    billing_profile_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """
    Override the billing profile for an event (admin only).

    Can only be done before invoice is generated.
    The billing profile must belong to the event's organizer.
    """
    # Get the event
    event_query = select(Event).where(Event.id == event_id, Event.is_deleted == False)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    # Check if invoice already exists for this event
    invoice_query = select(PlatformInvoice).where(PlatformInvoice.event_id == event_id)
    invoice_result = await db.execute(invoice_query)
    existing_invoice = invoice_result.scalar_one_or_none()

    if existing_invoice:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change billing profile after invoice has been generated",
        )

    # Verify the billing profile belongs to the event's organizer
    profile_query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.id == billing_profile_id,
        OrganizerBillingProfile.user_id == event.created_by_id,
        OrganizerBillingProfile.is_active == True,
    )
    profile_result = await db.execute(profile_query)
    billing_profile = profile_result.scalar_one_or_none()

    if not billing_profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Billing profile not found or does not belong to event organizer",
        )

    # Update the event's billing profile
    event.billing_profile_id = billing_profile_id
    await db.commit()

    return {
        "message": "Event billing profile updated successfully",
        "event_id": event_id,
        "billing_profile_id": billing_profile_id,
        "billing_profile_name": billing_profile.legal_name,
    }
