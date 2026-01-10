"""Webhook endpoints for external services.

Handles incoming webhooks from Stripe for payment and subscription events.
"""

import logging
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.billing import PlatformInvoice, InvoiceStatus
from app.models import UserAccount, ProSubscription, ProAuditLog, SubscriptionStatus, ProAction
from app.models.notification import UserDeviceToken
from app.services.push_notifications import send_silent_push

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()


async def _notify_pro_status_changed(db: AsyncSession, user_id: int, action: str):
    """Send silent push to user to refresh Pro status.

    Args:
        db: Database session
        user_id: User to notify
        action: What happened (created, updated, cancelled, renewed)
    """
    try:
        # Get user's device tokens
        query = select(UserDeviceToken).where(UserDeviceToken.user_id == user_id)
        result = await db.execute(query)
        tokens = result.scalars().all()

        if not tokens:
            logger.info(f"No device tokens for user {user_id}, skipping silent push")
            return

        # Send silent push to each device
        for device_token in tokens:
            send_silent_push(
                token=device_token.token,
                data={
                    "type": "pro_status_changed",
                    "action": action,
                },
            )

        logger.info(f"Sent silent push to {len(tokens)} devices for user {user_id}")
    except Exception as e:
        # Don't fail the webhook if push fails
        logger.error(f"Failed to send silent push to user {user_id}: {e}")


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle Stripe webhook events.

    Processes invoice payment events to update platform invoice status.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not settings.stripe_webhook_secret:
        logger.warning("Stripe webhook secret not configured")
        raise HTTPException(status_code=400, detail="Webhook not configured")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except ValueError as e:
        logger.error(f"Invalid webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid webhook signature: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event.type
    logger.info(f"Received Stripe webhook: {event_type}")

    # Handle invoice events (for platform billing)
    if event_type == "invoice.paid":
        await _handle_invoice_paid(db, event.data.object)

    elif event_type == "invoice.payment_failed":
        await _handle_invoice_payment_failed(db, event.data.object)

    elif event_type == "invoice.voided":
        await _handle_invoice_voided(db, event.data.object)

    elif event_type == "invoice.marked_uncollectible":
        await _handle_invoice_uncollectible(db, event.data.object)

    # Handle subscription events (for Pro subscriptions)
    elif event_type == "checkout.session.completed":
        await _handle_checkout_completed(db, event.data.object, event.id)

    elif event_type == "customer.subscription.created":
        await _handle_subscription_created(db, event.data.object, event.id)

    elif event_type == "customer.subscription.updated":
        await _handle_subscription_updated(db, event.data.object, event.id)

    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(db, event.data.object, event.id)

    return {"status": "success", "event_type": event_type}


async def _handle_invoice_paid(db: AsyncSession, stripe_invoice):
    """Update platform invoice when Stripe payment succeeds."""
    stripe_invoice_id = stripe_invoice.id

    query = select(PlatformInvoice).where(
        PlatformInvoice.stripe_invoice_id == stripe_invoice_id
    )
    result = await db.execute(query)
    invoice = result.scalar_one_or_none()

    if invoice:
        invoice.status = InvoiceStatus.PAID.value
        invoice.paid_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info(f"Invoice {invoice.id} marked as paid via webhook")
    else:
        logger.warning(f"No platform invoice found for Stripe invoice {stripe_invoice_id}")


async def _handle_invoice_payment_failed(db: AsyncSession, stripe_invoice):
    """Update platform invoice when payment fails."""
    stripe_invoice_id = stripe_invoice.id

    query = select(PlatformInvoice).where(
        PlatformInvoice.stripe_invoice_id == stripe_invoice_id
    )
    result = await db.execute(query)
    invoice = result.scalar_one_or_none()

    if invoice:
        # Check if past due date
        now = datetime.now(timezone.utc)
        if invoice.due_date and now > invoice.due_date:
            invoice.status = InvoiceStatus.OVERDUE.value
            await db.commit()
            logger.info(f"Invoice {invoice.id} marked as overdue via webhook")
        else:
            logger.info(f"Invoice {invoice.id} payment failed but not yet overdue")
    else:
        logger.warning(f"No platform invoice found for Stripe invoice {stripe_invoice_id}")


async def _handle_invoice_voided(db: AsyncSession, stripe_invoice):
    """Update platform invoice when voided in Stripe."""
    stripe_invoice_id = stripe_invoice.id

    query = select(PlatformInvoice).where(
        PlatformInvoice.stripe_invoice_id == stripe_invoice_id
    )
    result = await db.execute(query)
    invoice = result.scalar_one_or_none()

    if invoice:
        invoice.status = InvoiceStatus.CANCELLED.value
        invoice.cancelled_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info(f"Invoice {invoice.id} marked as cancelled via webhook")
    else:
        logger.warning(f"No platform invoice found for Stripe invoice {stripe_invoice_id}")


async def _handle_invoice_uncollectible(db: AsyncSession, stripe_invoice):
    """Update platform invoice when marked uncollectible."""
    stripe_invoice_id = stripe_invoice.id

    query = select(PlatformInvoice).where(
        PlatformInvoice.stripe_invoice_id == stripe_invoice_id
    )
    result = await db.execute(query)
    invoice = result.scalar_one_or_none()

    if invoice:
        invoice.status = InvoiceStatus.OVERDUE.value
        await db.commit()
        logger.info(f"Invoice {invoice.id} marked as overdue (uncollectible) via webhook")
    else:
        logger.warning(f"No platform invoice found for Stripe invoice {stripe_invoice_id}")


# ============== Pro Subscription Webhook Handlers ==============


async def _handle_checkout_completed(db: AsyncSession, session, event_id: str):
    """
    Handle checkout.session.completed event.

    This is fired when a customer completes the Stripe Checkout for a subscription.
    """
    logger.info(f"Processing checkout.session.completed: {session.id}, mode={session.mode}")

    # Only process subscription checkouts
    if session.mode != "subscription":
        logger.info(f"Skipping non-subscription checkout: {session.id}")
        return

    subscription_id = session.subscription
    customer_id = session.customer
    user_id_str = session.metadata.get("reelin_user_id") if session.metadata else None
    plan_type = session.metadata.get("plan_type", "monthly") if session.metadata else "monthly"

    logger.info(f"Checkout metadata: user_id={user_id_str}, plan={plan_type}, sub={subscription_id}")

    if not user_id_str:
        logger.warning(f"Checkout completed without reelin_user_id metadata: {session.id}")
        return

    user_id = int(user_id_str)

    # Get the subscription details from Stripe
    try:
        stripe_sub = stripe.Subscription.retrieve(subscription_id)
    except Exception as e:
        logger.error(f"Failed to retrieve subscription {subscription_id}: {e}")
        return

    # Find or create subscription record
    query = select(ProSubscription).where(
        ProSubscription.stripe_subscription_id == subscription_id
    )
    result = await db.execute(query)
    subscription = result.scalar_one_or_none()

    if not subscription:
        # Get price ID from subscription items
        items_data = stripe_sub.get("items", {}).get("data", [])
        price_id = items_data[0]["price"]["id"] if items_data else None

        subscription = ProSubscription(
            user_id=user_id,
            stripe_subscription_id=subscription_id,
            stripe_customer_id=customer_id,
            stripe_price_id=price_id,
            plan_type=plan_type,
            status=stripe_sub.status,
            current_period_start=datetime.fromtimestamp(stripe_sub.current_period_start, tz=timezone.utc),
            current_period_end=datetime.fromtimestamp(stripe_sub.current_period_end, tz=timezone.utc),
            cancel_at_period_end=stripe_sub.cancel_at_period_end,
        )
        db.add(subscription)
    else:
        subscription.status = stripe_sub.status
        subscription.current_period_start = datetime.fromtimestamp(stripe_sub.current_period_start, tz=timezone.utc)
        subscription.current_period_end = datetime.fromtimestamp(stripe_sub.current_period_end, tz=timezone.utc)

    # Update user's Pro status
    user_query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(user_query)
    user = result.scalar_one_or_none()

    if user:
        user.is_pro = True
        user.pro_expires_at = datetime.fromtimestamp(stripe_sub.current_period_end, tz=timezone.utc)
        user.pro_stripe_customer_id = customer_id
        user.pro_stripe_subscription_id = subscription_id
        user.pro_plan_type = plan_type
        user.pro_started_at = datetime.fromtimestamp(stripe_sub.current_period_start, tz=timezone.utc)

    # Log the action
    audit_log = ProAuditLog(
        admin_id=user_id,  # User themselves
        user_id=user_id,
        action=ProAction.SUBSCRIPTION_CREATED.value,
        details={
            "subscription_id": subscription_id,
            "plan_type": plan_type,
            "customer_id": customer_id,
        },
        stripe_event_id=event_id,
    )
    db.add(audit_log)

    await db.commit()
    logger.info(f"Pro subscription created for user {user_id}: {subscription_id}")

    # Notify user to refresh Pro status
    await _notify_pro_status_changed(db, user_id, "created")


async def _handle_subscription_created(db: AsyncSession, stripe_sub, event_id: str):
    """
    Handle customer.subscription.created event.

    Usually already handled by checkout.session.completed, but this handles edge cases.
    """
    subscription_id = stripe_sub.id
    customer_id = stripe_sub.customer
    user_id_str = stripe_sub.metadata.get("reelin_user_id")
    plan_type = stripe_sub.metadata.get("plan_type", "monthly")

    # Check if already exists
    query = select(ProSubscription).where(
        ProSubscription.stripe_subscription_id == subscription_id
    )
    result = await db.execute(query)
    existing = result.scalar_one_or_none()

    if existing:
        # Already processed
        return

    if not user_id_str:
        logger.warning(f"Subscription created without reelin_user_id metadata: {subscription_id}")
        return

    user_id = int(user_id_str)

    # Get price ID from subscription items
    items_data = stripe_sub.get("items", {}).get("data", [])
    price_id = items_data[0]["price"]["id"] if items_data else None

    subscription = ProSubscription(
        user_id=user_id,
        stripe_subscription_id=subscription_id,
        stripe_customer_id=customer_id,
        stripe_price_id=price_id,
        plan_type=plan_type,
        status=stripe_sub.status,
        current_period_start=datetime.fromtimestamp(stripe_sub.current_period_start, tz=timezone.utc),
        current_period_end=datetime.fromtimestamp(stripe_sub.current_period_end, tz=timezone.utc),
        cancel_at_period_end=stripe_sub.cancel_at_period_end,
    )
    db.add(subscription)

    # Update user's Pro status
    user_query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(user_query)
    user = result.scalar_one_or_none()

    if user:
        user.is_pro = True
        user.pro_expires_at = datetime.fromtimestamp(stripe_sub.current_period_end, tz=timezone.utc)
        user.pro_stripe_customer_id = customer_id
        user.pro_stripe_subscription_id = subscription_id
        user.pro_plan_type = plan_type
        user.pro_started_at = datetime.fromtimestamp(stripe_sub.current_period_start, tz=timezone.utc)

    await db.commit()
    logger.info(f"Pro subscription created (via created event) for user {user_id}: {subscription_id}")

    # Notify user to refresh Pro status
    await _notify_pro_status_changed(db, user_id, "created")


async def _handle_subscription_updated(db: AsyncSession, stripe_sub, event_id: str):
    """
    Handle customer.subscription.updated event.

    Handles renewals, plan changes, cancellation scheduling, etc.
    """
    subscription_id = stripe_sub.id

    query = select(ProSubscription).where(
        ProSubscription.stripe_subscription_id == subscription_id
    )
    result = await db.execute(query)
    subscription = result.scalar_one_or_none()

    if not subscription:
        logger.warning(f"Subscription updated but not found in DB: {subscription_id}")
        return

    # Update subscription record
    subscription.status = stripe_sub.status
    subscription.current_period_start = datetime.fromtimestamp(stripe_sub.current_period_start, tz=timezone.utc)
    subscription.current_period_end = datetime.fromtimestamp(stripe_sub.current_period_end, tz=timezone.utc)
    subscription.cancel_at_period_end = stripe_sub.cancel_at_period_end

    if stripe_sub.canceled_at:
        subscription.canceled_at = datetime.fromtimestamp(stripe_sub.canceled_at, tz=timezone.utc)

    if stripe_sub.ended_at:
        subscription.ended_at = datetime.fromtimestamp(stripe_sub.ended_at, tz=timezone.utc)

    # Update user's Pro status
    user_query = select(UserAccount).where(UserAccount.id == subscription.user_id)
    result = await db.execute(user_query)
    user = result.scalar_one_or_none()

    if user:
        is_active = stripe_sub.status in ["active", "trialing"]
        user.is_pro = is_active
        user.pro_expires_at = datetime.fromtimestamp(stripe_sub.current_period_end, tz=timezone.utc)

    # Log the action
    audit_log = ProAuditLog(
        admin_id=subscription.user_id,
        user_id=subscription.user_id,
        action=ProAction.SUBSCRIPTION_UPDATED.value,
        details={
            "subscription_id": subscription_id,
            "status": stripe_sub.status,
            "cancel_at_period_end": stripe_sub.cancel_at_period_end,
        },
        stripe_event_id=event_id,
    )
    db.add(audit_log)

    await db.commit()
    logger.info(f"Pro subscription updated for user {subscription.user_id}: {subscription_id} -> {stripe_sub.status}")

    # Notify user to refresh Pro status
    await _notify_pro_status_changed(db, subscription.user_id, "updated")


async def _handle_subscription_deleted(db: AsyncSession, stripe_sub, event_id: str):
    """
    Handle customer.subscription.deleted event.

    Subscription has been fully cancelled (past period end or immediately).
    """
    subscription_id = stripe_sub.id

    query = select(ProSubscription).where(
        ProSubscription.stripe_subscription_id == subscription_id
    )
    result = await db.execute(query)
    subscription = result.scalar_one_or_none()

    if not subscription:
        logger.warning(f"Subscription deleted but not found in DB: {subscription_id}")
        return

    # Update subscription record
    subscription.status = SubscriptionStatus.CANCELED.value
    subscription.ended_at = datetime.now(timezone.utc)

    # Update user's Pro status
    user_query = select(UserAccount).where(UserAccount.id == subscription.user_id)
    result = await db.execute(user_query)
    user = result.scalar_one_or_none()

    if user:
        # Check if user has other active subscriptions or grants before removing Pro
        other_sub_query = select(ProSubscription).where(
            ProSubscription.user_id == user.id,
            ProSubscription.id != subscription.id,
            ProSubscription.status.in_(["active", "trialing"]),
        )
        result = await db.execute(other_sub_query)
        other_subs = result.scalars().all()

        if not other_subs:
            # No other active subscriptions, check for grants
            from app.models import ProGrant
            grant_query = select(ProGrant).where(
                ProGrant.user_id == user.id,
                ProGrant.is_active == True,
            )
            result = await db.execute(grant_query)
            grants = result.scalars().all()

            # Check if any grant is still valid
            now = datetime.now(timezone.utc)
            has_valid_grant = any(
                g.expires_at is None or g.expires_at > now
                for g in grants
            )

            if not has_valid_grant:
                user.is_pro = False
                user.pro_expires_at = None
                user.pro_stripe_subscription_id = None

    # Log the action
    audit_log = ProAuditLog(
        admin_id=subscription.user_id,
        user_id=subscription.user_id,
        action=ProAction.SUBSCRIPTION_CANCELLED.value,
        details={
            "subscription_id": subscription_id,
        },
        stripe_event_id=event_id,
    )
    db.add(audit_log)

    await db.commit()
    logger.info(f"Pro subscription cancelled for user {subscription.user_id}: {subscription_id}")

    # Notify user to refresh Pro status
    await _notify_pro_status_changed(db, subscription.user_id, "cancelled")
