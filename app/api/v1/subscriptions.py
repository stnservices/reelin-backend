"""Subscription API endpoints for ReelIn Pro.

Handles Pro subscription checkout, status, and management via Stripe.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import UserAccount, ProSubscription, ProGrant, SubscriptionStatus
from app.services.stripe_subscription import stripe_subscription_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


# ============== Request/Response Schemas ==============


class CheckoutRequest(BaseModel):
    """Request to create a checkout session."""

    plan_type: str  # 'monthly' or 'yearly'
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class CheckoutResponse(BaseModel):
    """Checkout session response."""

    url: str
    session_id: str


class PortalResponse(BaseModel):
    """Customer portal session response."""

    url: str


class PlanInfo(BaseModel):
    """Subscription plan info."""

    id: str
    name: str
    price: float
    currency: str
    interval: str
    description: Optional[str] = None


class ProStatusResponse(BaseModel):
    """Pro subscription status response."""

    is_pro: bool
    source: Optional[str] = None  # 'stripe', 'manual', None
    plan_type: Optional[str] = None
    expires_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    cancel_at_period_end: bool = False
    stripe_subscription_id: Optional[str] = None
    stripe_customer_id: Optional[str] = None


class CancelRequest(BaseModel):
    """Cancel subscription request."""

    immediate: bool = False


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str


# ============== Endpoints ==============


@router.get("/plans", response_model=list[PlanInfo])
async def get_plans():
    """
    Get available Pro subscription plans.

    Returns list of available plans with pricing.
    """
    plans = stripe_subscription_service.get_plans()
    return [
        PlanInfo(
            id=p["id"],
            name=p["name"],
            price=p["price"],
            currency=p["currency"],
            interval=p["interval"],
            description=p.get("description"),
        )
        for p in plans
    ]


@router.get("/status", response_model=ProStatusResponse)
async def get_pro_status(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get current user's Pro subscription status.

    Returns whether user is Pro and subscription details.
    """
    # Check for active Stripe subscription
    subscription_query = select(ProSubscription).where(
        ProSubscription.user_id == current_user.id,
        ProSubscription.status.in_([
            SubscriptionStatus.ACTIVE.value,
            SubscriptionStatus.TRIALING.value,
        ]),
    )
    result = await db.execute(subscription_query)
    subscription = result.scalar_one_or_none()

    if subscription:
        return ProStatusResponse(
            is_pro=True,
            source="stripe",
            plan_type=subscription.plan_type,
            expires_at=subscription.current_period_end,
            started_at=subscription.current_period_start,
            cancel_at_period_end=subscription.cancel_at_period_end,
            stripe_subscription_id=subscription.stripe_subscription_id,
            stripe_customer_id=subscription.stripe_customer_id,
        )

    # Check for active manual grant
    now = datetime.now(timezone.utc)
    grant_query = select(ProGrant).where(
        ProGrant.user_id == current_user.id,
        ProGrant.is_active == True,
        or_(
            ProGrant.expires_at.is_(None),  # Lifetime
            ProGrant.expires_at > now,
        ),
    )
    result = await db.execute(grant_query)
    grant = result.scalar_one_or_none()

    if grant:
        return ProStatusResponse(
            is_pro=True,
            source="manual",
            plan_type=grant.grant_type,
            expires_at=grant.expires_at,
            started_at=grant.starts_at,
            cancel_at_period_end=False,
        )

    # Not Pro
    return ProStatusResponse(
        is_pro=False,
        source=None,
        plan_type=None,
    )


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    request: CheckoutRequest,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a Stripe Checkout session for Pro subscription.

    Returns URL to redirect user to Stripe Checkout.
    """
    if request.plan_type not in ["monthly", "yearly"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid plan type. Must be 'monthly' or 'yearly'.",
        )

    # Check if user already has active subscription
    subscription_query = select(ProSubscription).where(
        ProSubscription.user_id == current_user.id,
        ProSubscription.status.in_([
            SubscriptionStatus.ACTIVE.value,
            SubscriptionStatus.TRIALING.value,
        ]),
    )
    result = await db.execute(subscription_query)
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You already have an active subscription. Use the customer portal to manage it.",
        )

    try:
        # Get existing customer ID if available
        customer_id = current_user.pro_stripe_customer_id

        url, session_id = await stripe_subscription_service.create_checkout_session(
            user_id=current_user.id,
            email=current_user.email,
            plan_type=request.plan_type,
            customer_id=customer_id,
            success_url=request.success_url,
            cancel_url=request.cancel_url,
        )

        return CheckoutResponse(url=url, session_id=session_id)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Checkout creation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create checkout session. Please try again.",
        )


@router.post("/portal", response_model=PortalResponse)
async def create_portal_session(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a Stripe Customer Portal session for subscription management.

    Returns URL to redirect user to Stripe Portal.
    """
    # Get customer ID from subscription or user
    customer_id = current_user.pro_stripe_customer_id

    if not customer_id:
        # Check if they have a subscription
        subscription_query = select(ProSubscription).where(
            ProSubscription.user_id == current_user.id,
        ).order_by(ProSubscription.created_at.desc())
        result = await db.execute(subscription_query)
        subscription = result.scalar_one_or_none()

        if subscription:
            customer_id = subscription.stripe_customer_id

    if not customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No subscription found. You need to subscribe first.",
        )

    try:
        url = await stripe_subscription_service.create_portal_session(customer_id)
        return PortalResponse(url=url)

    except Exception as e:
        logger.error(f"Portal session creation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create portal session. Please try again.",
        )


@router.post("/cancel", response_model=MessageResponse)
async def cancel_subscription(
    request: CancelRequest,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel current subscription.

    By default cancels at end of billing period. Use immediate=true to cancel now.
    """
    # Get active subscription
    subscription_query = select(ProSubscription).where(
        ProSubscription.user_id == current_user.id,
        ProSubscription.status.in_([
            SubscriptionStatus.ACTIVE.value,
            SubscriptionStatus.TRIALING.value,
        ]),
    )
    result = await db.execute(subscription_query)
    subscription = result.scalar_one_or_none()

    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active subscription found.",
        )

    try:
        result = await stripe_subscription_service.cancel_subscription(
            subscription.stripe_subscription_id,
            cancel_immediately=request.immediate,
        )

        # Update local record
        subscription.cancel_at_period_end = result.get("cancel_at_period_end", False)
        if request.immediate:
            subscription.status = SubscriptionStatus.CANCELED.value
            subscription.ended_at = datetime.now(timezone.utc)
        subscription.canceled_at = datetime.now(timezone.utc)
        await db.commit()

        if request.immediate:
            return MessageResponse(message="Subscription cancelled immediately.")
        else:
            return MessageResponse(
                message=f"Subscription will be cancelled at end of billing period ({result.get('current_period_end')})."
            )

    except Exception as e:
        logger.error(f"Subscription cancellation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel subscription. Please try again.",
        )


@router.post("/resume", response_model=MessageResponse)
async def resume_subscription(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Resume a cancelled subscription (if still in current billing period).
    """
    # Get subscription that's cancelled but not yet ended
    subscription_query = select(ProSubscription).where(
        ProSubscription.user_id == current_user.id,
        ProSubscription.status == SubscriptionStatus.ACTIVE.value,
        ProSubscription.cancel_at_period_end == True,
    )
    result = await db.execute(subscription_query)
    subscription = result.scalar_one_or_none()

    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No cancelled subscription found that can be resumed.",
        )

    try:
        await stripe_subscription_service.resume_subscription(
            subscription.stripe_subscription_id
        )

        # Update local record
        subscription.cancel_at_period_end = False
        subscription.canceled_at = None
        await db.commit()

        return MessageResponse(message="Subscription resumed successfully.")

    except Exception as e:
        logger.error(f"Subscription resume failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resume subscription. Please try again.",
        )
