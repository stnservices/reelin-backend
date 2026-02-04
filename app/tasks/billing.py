"""Celery tasks for billing operations.

Handles invoice generation when events are completed.
"""

import asyncio
import logging
import random
import traceback
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from celery import shared_task
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.celery_app import celery_app
from app.database import async_session_maker
from app.models.billing import (
    OrganizerBillingProfile,
    PricingTier,
    PlatformInvoice,
    InvoiceStatus,
    PricingModel,
)
from app.models.event import Event, EventStatus
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.services.stripe_billing import stripe_billing_service

logger = logging.getLogger(__name__)


def generate_invoice_number() -> str:
    """
    Generate unique invoice number: REELIN-YYYYMM-XXXXX

    Format: REELIN-{YEAR}{MONTH}-{RANDOM 5 DIGITS}
    """
    now = datetime.now(timezone.utc)
    prefix = f"REELIN-{now.strftime('%Y%m')}"
    suffix = str(random.randint(10000, 99999))
    return f"{prefix}-{suffix}"


@celery_app.task(bind=True, max_retries=3)
def generate_event_invoice(self, event_id: int):
    """
    Generate and optionally send an invoice for a completed event.

    This task is triggered when an event status changes to COMPLETED.

    Steps:
    1. Verify the event is completed
    2. Check for existing invoice (prevent duplicates)
    3. Get organizer's billing profile
    4. Find applicable pricing tier for event type
    5. Count approved participants
    6. Calculate amount based on pricing model
    7. Create PlatformInvoice record
    8. Create Stripe invoice and send

    Args:
        event_id: The ID of the completed event
    """
    logger.info(f"Generating invoice for event {event_id}")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_async_generate_invoice(event_id))
            return result
        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Failed to generate invoice for event {event_id}: {e}\n{traceback.format_exc()}")
        raise self.retry(exc=e, countdown=30)


async def _async_generate_invoice(event_id: int) -> dict:
    """Async implementation of invoice generation."""

    async with async_session_maker() as db:
        # 1. Get event with organizer
        event_query = (
            select(Event)
            .options(selectinload(Event.event_type))
            .where(Event.id == event_id)
        )
        event_result = await db.execute(event_query)
        event = event_result.scalar_one_or_none()

        if not event:
            logger.error(f"Event {event_id} not found")
            return {"error": "Event not found", "event_id": event_id}

        if event.status != EventStatus.COMPLETED.value:
            logger.warning(f"Event {event_id} is not completed (status: {event.status})")
            return {"error": "Event not completed", "status": event.status}

        # 2. Check for existing invoice
        existing_query = select(PlatformInvoice).where(
            PlatformInvoice.event_id == event_id
        )
        existing_result = await db.execute(existing_query)
        existing = existing_result.scalar_one_or_none()

        if existing:
            logger.info(f"Invoice already exists for event {event_id}: {existing.invoice_number}")
            return {
                "error": "Invoice already exists",
                "invoice_id": existing.id,
                "invoice_number": existing.invoice_number,
            }

        # 3. Get billing profile - use event.billing_profile_id if set, otherwise fallback
        billing_profile = None

        # First: Try to use the event's assigned billing profile
        if event.billing_profile_id:
            profile_query = select(OrganizerBillingProfile).where(
                OrganizerBillingProfile.id == event.billing_profile_id,
                OrganizerBillingProfile.is_active == True,
            )
            profile_result = await db.execute(profile_query)
            billing_profile = profile_result.scalar_one_or_none()

            if billing_profile:
                logger.info(
                    f"Using event's assigned billing profile {billing_profile.id} "
                    f"for event {event_id}"
                )

        # Fallback: Look up organizer's primary profile
        if not billing_profile:
            profile_query = select(OrganizerBillingProfile).where(
                OrganizerBillingProfile.user_id == event.created_by_id,
                OrganizerBillingProfile.is_active == True,
                OrganizerBillingProfile.is_primary == True,
            )
            profile_result = await db.execute(profile_query)
            billing_profile = profile_result.scalar_one_or_none()

            if billing_profile:
                logger.info(
                    f"Using organizer's primary billing profile {billing_profile.id} "
                    f"for event {event_id} (event had no assigned profile)"
                )

        # Fallback: Use oldest profile by created_at
        if not billing_profile:
            profile_query = (
                select(OrganizerBillingProfile)
                .where(
                    OrganizerBillingProfile.user_id == event.created_by_id,
                    OrganizerBillingProfile.is_active == True,
                )
                .order_by(OrganizerBillingProfile.created_at.asc())
                .limit(1)
            )
            profile_result = await db.execute(profile_query)
            billing_profile = profile_result.scalar_one_or_none()

            if billing_profile:
                logger.info(
                    f"Using organizer's oldest billing profile {billing_profile.id} "
                    f"for event {event_id} (no primary profile found)"
                )

        if not billing_profile:
            logger.warning(f"No billing profile found for organizer {event.created_by_id}")
            return {
                "error": "No billing profile",
                "organizer_id": event.created_by_id,
            }

        # 4. Get active pricing tier for this event type
        tier_query = (
            select(PricingTier)
            .options(selectinload(PricingTier.currency))
            .where(
                PricingTier.billing_profile_id == billing_profile.id,
                PricingTier.event_type_id == event.event_type_id,
                PricingTier.effective_until.is_(None),  # Active tier
            )
        )
        tier_result = await db.execute(tier_query)
        pricing_tier = tier_result.scalar_one_or_none()

        if not pricing_tier:
            logger.warning(
                f"No pricing tier found for billing profile {billing_profile.id}, "
                f"event type {event.event_type_id}"
            )
            return {
                "error": "No pricing tier",
                "billing_profile_id": billing_profile.id,
                "event_type_id": event.event_type_id,
            }

        # 5. Count approved participants
        count_query = select(func.count(EventEnrollment.id)).where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
        count_result = await db.execute(count_query)
        participant_count = count_result.scalar() or 0

        # 6. Calculate amount
        if pricing_tier.pricing_model == PricingModel.PER_PARTICIPANT.value:
            subtotal = pricing_tier.rate * participant_count
            # Apply minimum charge if configured
            if pricing_tier.minimum_charge and subtotal < pricing_tier.minimum_charge:
                subtotal = pricing_tier.minimum_charge
        else:  # FIXED
            subtotal = pricing_tier.rate

        total_amount = subtotal  # No adjustments in automatic generation

        # 7. Create invoice record
        invoice_number = generate_invoice_number()

        # Ensure unique invoice number
        while True:
            check_query = select(PlatformInvoice).where(
                PlatformInvoice.invoice_number == invoice_number
            )
            check_result = await db.execute(check_query)
            if not check_result.scalar_one_or_none():
                break
            invoice_number = generate_invoice_number()

        invoice = PlatformInvoice(
            invoice_number=invoice_number,
            billing_profile_id=billing_profile.id,
            event_id=event_id,
            pricing_tier_id=pricing_tier.id,
            pricing_model_snapshot=pricing_tier.pricing_model,
            rate_snapshot=pricing_tier.rate,
            participant_count=participant_count,
            subtotal=subtotal,
            discount_amount=Decimal("0"),
            adjustment_amount=Decimal("0"),
            total_amount=total_amount,
            currency_code=pricing_tier.currency.code,
            status=InvoiceStatus.DRAFT.value,
            line_items=[
                {
                    "description": f"Platform fee - {event.name}",
                    "quantity": participant_count if pricing_tier.pricing_model == PricingModel.PER_PARTICIPANT.value else 1,
                    "unit_price": float(pricing_tier.rate),
                    "amount": float(subtotal),
                }
            ],
        )
        db.add(invoice)
        await db.commit()
        await db.refresh(invoice)

        logger.info(
            f"Created invoice {invoice.invoice_number} for event {event_id}: "
            f"{total_amount} {pricing_tier.currency.code}"
        )

        # 8. Create Stripe invoice and send
        try:
            stripe_result = await stripe_billing_service.create_invoice(
                invoice=invoice,
                billing_profile=billing_profile,
                event_name=event.name,
                auto_send=True,  # Automatically send to organizer
            )

            if stripe_result.get("stripe_invoice_id"):
                invoice.stripe_invoice_id = stripe_result["stripe_invoice_id"]
                invoice.stripe_invoice_url = stripe_result.get("hosted_invoice_url")
                invoice.stripe_pdf_url = stripe_result.get("invoice_pdf")
                invoice.status = InvoiceStatus.PENDING.value
                invoice.issued_at = datetime.now(timezone.utc)
                invoice.due_date = datetime.now(timezone.utc) + timedelta(days=30)
                await db.commit()

                logger.info(
                    f"Stripe invoice created and sent for {invoice.invoice_number}: "
                    f"{stripe_result['stripe_invoice_id']}"
                )

                return {
                    "success": True,
                    "invoice_id": invoice.id,
                    "invoice_number": invoice.invoice_number,
                    "stripe_invoice_id": stripe_result["stripe_invoice_id"],
                    "amount": float(total_amount),
                    "currency": pricing_tier.currency.code,
                }
            else:
                # Stripe not configured, invoice created but not sent
                logger.info(
                    f"Invoice {invoice.invoice_number} created but Stripe not configured"
                )
                return {
                    "success": True,
                    "invoice_id": invoice.id,
                    "invoice_number": invoice.invoice_number,
                    "stripe_invoice_id": None,
                    "amount": float(total_amount),
                    "currency": pricing_tier.currency.code,
                    "note": "Stripe not configured, manual sending required",
                }

        except Exception as e:
            # Invoice created but Stripe failed - admin will need to manually send
            logger.error(f"Stripe invoice creation failed: {e}")
            return {
                "success": True,
                "invoice_id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "stripe_error": str(e),
                "note": "Invoice created, Stripe failed - manual sending required",
            }


@celery_app.task(bind=True)
def check_overdue_invoices(self):
    """
    Periodic task to check for overdue invoices and update their status.

    Should be run daily via Celery Beat.
    """
    logger.info("Checking for overdue invoices")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_async_check_overdue())
            return result
        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Failed to check overdue invoices: {e}")
        raise


async def _async_check_overdue() -> dict:
    """Async implementation of overdue invoice check."""

    async with async_session_maker() as db:
        now = datetime.now(timezone.utc)

        # Find pending invoices past due date
        query = select(PlatformInvoice).where(
            PlatformInvoice.status == InvoiceStatus.PENDING.value,
            PlatformInvoice.due_date < now,
        )
        result = await db.execute(query)
        overdue_invoices = result.scalars().all()

        count = 0
        for invoice in overdue_invoices:
            invoice.status = InvoiceStatus.OVERDUE.value
            count += 1

        if count > 0:
            await db.commit()
            logger.info(f"Marked {count} invoices as overdue")

        return {"overdue_count": count}
