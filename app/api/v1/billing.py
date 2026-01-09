"""Organizer billing API endpoints.

These endpoints allow organizers to view their billing profile,
pricing rates, and invoices.
"""

from math import ceil
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, update
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
from app.models.organizer_permissions import OrganizerEventTypeAccess
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
    EventTypeDefaultUpdate,
    EventTypeDefaultResponse,
    EventTypeDefaultsListResponse,
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
        is_primary=profile.is_primary,
        organizer_type=profile.organizer_type,
        legal_name=profile.legal_name,
        cnp=profile.cnp,
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


def validate_cnp_for_organizer_type(organizer_type: str, cnp: Optional[str]) -> None:
    """Validate CNP is provided for individual organizer type."""
    if organizer_type == "individual" and not cnp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CNP is required for individual organizer type",
        )
    if cnp and not re.match(r"^[1-8]\d{12}$", cnp):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid CNP format. Must be 13 digits starting with 1-8",
        )


@router.get("/profiles", response_model=List[BillingProfileResponse])
async def get_my_billing_profiles(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get all billing profiles for the current user.
    Returns empty list if no billing profiles exist.
    """
    query = (
        select(OrganizerBillingProfile)
        .where(OrganizerBillingProfile.user_id == current_user.id)
        .order_by(OrganizerBillingProfile.is_primary.desc(), OrganizerBillingProfile.created_at)
    )
    result = await db.execute(query)
    profiles = result.scalars().all()

    return [billing_profile_to_response(p) for p in profiles]


@router.get("/profile", response_model=Optional[BillingProfileResponse])
async def get_my_billing_profile(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get the current user's primary billing profile.
    Returns null if no billing profile exists.
    Deprecated: Use GET /profiles instead.
    """
    query = (
        select(OrganizerBillingProfile)
        .where(OrganizerBillingProfile.user_id == current_user.id)
        .order_by(OrganizerBillingProfile.is_primary.desc(), OrganizerBillingProfile.created_at)
    )
    result = await db.execute(query)
    profile = result.scalars().first()

    if not profile:
        return None

    return billing_profile_to_response(profile)


@router.post("/profiles", response_model=BillingProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_billing_profile(
    profile_data: BillingProfileCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Create a new billing profile for the current user.
    First profile is automatically marked as primary.
    Creates a new Stripe customer for the profile.
    """
    # Validate CNP for individual organizer type
    validate_cnp_for_organizer_type(profile_data.organizer_type, profile_data.cnp)

    # Check if this is the first profile (will be primary)
    count_query = select(func.count(OrganizerBillingProfile.id)).where(
        OrganizerBillingProfile.user_id == current_user.id
    )
    count_result = await db.execute(count_query)
    existing_count = count_result.scalar() or 0
    is_first_profile = existing_count == 0

    # Create the profile
    profile = OrganizerBillingProfile(
        user_id=current_user.id,
        is_primary=is_first_profile,
        organizer_type=profile_data.organizer_type,
        legal_name=profile_data.legal_name,
        cnp=profile_data.cnp,
        tax_id=profile_data.tax_id,
        registration_number=profile_data.registration_number,
        billing_address_line1=profile_data.billing_address_line1,
        billing_address_line2=profile_data.billing_address_line2,
        billing_city=profile_data.billing_city,
        billing_county=profile_data.billing_county,
        billing_postal_code=profile_data.billing_postal_code,
        billing_country=profile_data.billing_country,
        billing_email=profile_data.billing_email,
        billing_phone=profile_data.billing_phone,
        is_vat_payer=profile_data.is_vat_payer,
        vat_rate=profile_data.vat_rate,
    )

    db.add(profile)
    await db.flush()

    # Create Stripe customer for this billing profile
    try:
        stripe_customer = await stripe_billing_service.create_customer(profile)
        if stripe_customer:
            profile.stripe_customer_id = stripe_customer.id
    except Exception as e:
        # Log but don't fail - Stripe customer can be created later
        pass

    await db.commit()
    await db.refresh(profile)

    return billing_profile_to_response(profile)


@router.patch("/profiles/{profile_id}", response_model=BillingProfileResponse)
async def update_billing_profile(
    profile_id: int,
    profile_data: BillingProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Update a specific billing profile.
    Only updates provided fields.
    Setting is_primary=true will clear is_primary on other profiles.
    """
    # Get the profile (must belong to current user)
    query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.id == profile_id,
        OrganizerBillingProfile.user_id == current_user.id,
    )
    result = await db.execute(query)
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing profile not found",
        )

    # Get update data
    update_data = profile_data.model_dump(exclude_unset=True)

    # Validate CNP if organizer_type is being changed to individual or CNP is being updated
    new_organizer_type = update_data.get("organizer_type", profile.organizer_type)
    new_cnp = update_data.get("cnp", profile.cnp)
    if "organizer_type" in update_data or "cnp" in update_data:
        validate_cnp_for_organizer_type(new_organizer_type, new_cnp)

    # Handle is_primary update
    if update_data.get("is_primary") is True:
        # Clear is_primary on all other profiles for this user
        clear_stmt = (
            update(OrganizerBillingProfile)
            .where(
                OrganizerBillingProfile.user_id == current_user.id,
                OrganizerBillingProfile.id != profile_id,
            )
            .values(is_primary=False)
        )
        await db.execute(clear_stmt)

    # Update profile fields
    for field, value in update_data.items():
        # Don't allow updating notes (admin only)
        if field != "notes" and value is not None:
            setattr(profile, field, value)

    # Update Stripe customer if we have one
    if profile.stripe_customer_id:
        try:
            await stripe_billing_service.update_customer(
                profile.stripe_customer_id, profile
            )
        except Exception:
            pass  # Log but don't fail

    await db.commit()
    await db.refresh(profile)

    return billing_profile_to_response(profile)


@router.post("/profiles/{profile_id}/set-primary", response_model=BillingProfileResponse)
async def set_profile_as_primary(
    profile_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Set a billing profile as the primary profile.
    Clears is_primary on all other profiles for this user.
    """
    # Get the profile (must belong to current user)
    query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.id == profile_id,
        OrganizerBillingProfile.user_id == current_user.id,
    )
    result = await db.execute(query)
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing profile not found",
        )

    # Clear is_primary on all other profiles
    clear_stmt = (
        update(OrganizerBillingProfile)
        .where(
            OrganizerBillingProfile.user_id == current_user.id,
            OrganizerBillingProfile.id != profile_id,
        )
        .values(is_primary=False)
    )
    await db.execute(clear_stmt)

    # Set this profile as primary
    profile.is_primary = True
    await db.commit()
    await db.refresh(profile)

    return billing_profile_to_response(profile)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_200_OK)
async def delete_billing_profile(
    profile_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Delete a billing profile.
    Cannot delete the primary profile if it's the only one.
    Cannot delete a profile that has associated invoices.
    """
    # Get the profile (must belong to current user)
    query = select(OrganizerBillingProfile).where(
        OrganizerBillingProfile.id == profile_id,
        OrganizerBillingProfile.user_id == current_user.id,
    )
    result = await db.execute(query)
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing profile not found",
        )

    # Check if it's the primary profile
    if profile.is_primary:
        # Count other profiles
        count_query = select(func.count()).select_from(OrganizerBillingProfile).where(
            OrganizerBillingProfile.user_id == current_user.id,
            OrganizerBillingProfile.id != profile_id,
        )
        count_result = await db.execute(count_query)
        other_count = count_result.scalar()

        if other_count == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete the only billing profile. Create another profile first.",
            )

    # Check for associated invoices
    invoice_query = select(func.count()).select_from(PlatformInvoice).where(
        PlatformInvoice.billing_profile_id == profile_id
    )
    invoice_result = await db.execute(invoice_query)
    invoice_count = invoice_result.scalar()

    if invoice_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete profile with {invoice_count} associated invoice(s). Deactivate it instead.",
        )

    # Delete the profile
    await db.delete(profile)
    await db.commit()

    # If we deleted the primary profile, set another one as primary
    if profile.is_primary:
        first_profile_query = select(OrganizerBillingProfile).where(
            OrganizerBillingProfile.user_id == current_user.id
        ).order_by(OrganizerBillingProfile.created_at).limit(1)
        first_result = await db.execute(first_profile_query)
        first_profile = first_result.scalar_one_or_none()
        if first_profile:
            first_profile.is_primary = True
            await db.commit()

    return {"message": "Billing profile deleted successfully"}


@router.put("/profile", response_model=BillingProfileResponse)
async def update_my_billing_profile(
    profile_data: BillingProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Update the current user's primary billing profile.
    Deprecated: Use PATCH /profiles/{profile_id} instead.
    """
    # Get primary profile (or oldest)
    query = (
        select(OrganizerBillingProfile)
        .where(OrganizerBillingProfile.user_id == current_user.id)
        .order_by(OrganizerBillingProfile.is_primary.desc(), OrganizerBillingProfile.created_at)
    )
    result = await db.execute(query)
    profile = result.scalars().first()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No billing profile found. Contact admin to create one.",
        )

    # Get update data
    update_data = profile_data.model_dump(exclude_unset=True)

    # Validate CNP
    new_organizer_type = update_data.get("organizer_type", profile.organizer_type)
    new_cnp = update_data.get("cnp", profile.cnp)
    if "organizer_type" in update_data or "cnp" in update_data:
        validate_cnp_for_organizer_type(new_organizer_type, new_cnp)

    # Handle is_primary update
    if update_data.get("is_primary") is True:
        clear_stmt = (
            update(OrganizerBillingProfile)
            .where(
                OrganizerBillingProfile.user_id == current_user.id,
                OrganizerBillingProfile.id != profile.id,
            )
            .values(is_primary=False)
        )
        await db.execute(clear_stmt)

    # Update only provided fields
    for field, value in update_data.items():
        if field != "notes" and value is not None:
            setattr(profile, field, value)

    # Update Stripe customer if we have one
    if profile.stripe_customer_id:
        try:
            await stripe_billing_service.update_customer(
                profile.stripe_customer_id, profile
            )
        except Exception:
            pass

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


# ============== Event Type Default Billing Profile Endpoints ==============


@router.get("/event-type-defaults", response_model=EventTypeDefaultsListResponse)
async def get_my_event_type_defaults(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get list of event types the organizer has access to, with default billing profiles.

    Returns all event type access records for the current user with their
    default billing profile assignments.
    """
    query = (
        select(OrganizerEventTypeAccess)
        .options(
            selectinload(OrganizerEventTypeAccess.event_type),
            selectinload(OrganizerEventTypeAccess.default_billing_profile),
        )
        .where(
            OrganizerEventTypeAccess.user_id == current_user.id,
            OrganizerEventTypeAccess.is_active == True,
        )
        .order_by(OrganizerEventTypeAccess.granted_at.desc())
    )

    result = await db.execute(query)
    access_records = result.scalars().all()

    items = []
    for access in access_records:
        items.append(
            EventTypeDefaultResponse(
                event_type_id=access.event_type_id,
                event_type_name=access.event_type.name if access.event_type else "Unknown",
                event_type_code=access.event_type.code if access.event_type else "unknown",
                default_billing_profile_id=access.default_billing_profile_id,
                default_billing_profile_name=(
                    access.default_billing_profile.legal_name
                    if access.default_billing_profile
                    else None
                ),
                granted_at=access.granted_at,
            )
        )

    return EventTypeDefaultsListResponse(items=items, total=len(items))


@router.patch("/event-type-defaults/{event_type_id}", response_model=EventTypeDefaultResponse)
async def update_event_type_default(
    event_type_id: int,
    update_data: EventTypeDefaultUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Set or clear the default billing profile for an event type.

    Organizers can use this to specify which billing profile should be
    automatically assigned when creating events of this type.
    """
    # Find the event type access record
    access_query = (
        select(OrganizerEventTypeAccess)
        .options(
            selectinload(OrganizerEventTypeAccess.event_type),
            selectinload(OrganizerEventTypeAccess.default_billing_profile),
        )
        .where(
            OrganizerEventTypeAccess.user_id == current_user.id,
            OrganizerEventTypeAccess.event_type_id == event_type_id,
            OrganizerEventTypeAccess.is_active == True,
        )
    )
    access_result = await db.execute(access_query)
    access = access_result.scalar_one_or_none()

    if not access:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You don't have access to this event type",
        )

    # If setting a billing profile, verify it exists and belongs to the user
    if update_data.billing_profile_id is not None:
        profile_query = select(OrganizerBillingProfile).where(
            OrganizerBillingProfile.id == update_data.billing_profile_id,
            OrganizerBillingProfile.user_id == current_user.id,
            OrganizerBillingProfile.is_active == True,
        )
        profile_result = await db.execute(profile_query)
        profile = profile_result.scalar_one_or_none()

        if not profile:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid billing profile or not owned by you",
            )

    # Update the default billing profile
    access.default_billing_profile_id = update_data.billing_profile_id
    await db.commit()

    # Refresh to get updated relationships
    await db.refresh(access)

    # Re-query to get the updated relationship
    access_query = (
        select(OrganizerEventTypeAccess)
        .options(
            selectinload(OrganizerEventTypeAccess.event_type),
            selectinload(OrganizerEventTypeAccess.default_billing_profile),
        )
        .where(OrganizerEventTypeAccess.id == access.id)
    )
    access_result = await db.execute(access_query)
    access = access_result.scalar_one()

    return EventTypeDefaultResponse(
        event_type_id=access.event_type_id,
        event_type_name=access.event_type.name if access.event_type else "Unknown",
        event_type_code=access.event_type.code if access.event_type else "unknown",
        default_billing_profile_id=access.default_billing_profile_id,
        default_billing_profile_name=(
            access.default_billing_profile.legal_name
            if access.default_billing_profile
            else None
        ),
        granted_at=access.granted_at,
    )
