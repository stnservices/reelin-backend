"""Stripe Subscription integration service for ReelIn Pro.

This module handles all Stripe-related operations for Pro subscriptions,
including checkout sessions, customer portal, and subscription management.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any

import stripe
from stripe.error import StripeError

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Default prices (used if DB settings unavailable)
# Actual prices are fetched from admin Pro settings
DEFAULT_PRICES = {
    "monthly": {
        "amount": 299,  # €2.99 in cents
        "currency": "eur",
        "interval": "month",
    },
    "yearly": {
        "amount": 1999,  # €19.99 in cents
        "currency": "eur",
        "interval": "year",
    },
}

# Cache for Stripe price IDs (keyed by amount to handle price changes)
# Format: {(amount, interval): price_id}
_stripe_price_cache: Dict[tuple, str] = {}


class StripeSubscriptionService:
    """Service for Stripe subscription operations."""

    def __init__(self):
        """Initialize Stripe with API key."""
        stripe.api_key = settings.stripe_secret_key
        self._product_id: Optional[str] = None

    def _is_configured(self) -> bool:
        """Check if Stripe is properly configured."""
        return bool(settings.stripe_secret_key)

    async def _ensure_product(self) -> str:
        """Ensure the ReelIn Pro product exists in Stripe and return its ID."""
        if self._product_id:
            return self._product_id

        if not self._is_configured():
            raise ValueError("Stripe is not configured")

        try:
            # Search for existing product
            products = stripe.Product.list(active=True, limit=100)
            for product in products.data:
                if product.metadata.get("reelin_product") == "pro":
                    self._product_id = product.id
                    return self._product_id

            # Create product if not exists
            reelin_pro = stripe.Product.create(
                name="ReelIn Pro",
                description="Premium features for ReelIn fishing competition app",
                metadata={"reelin_product": "pro"},
            )
            self._product_id = reelin_pro.id
            logger.info(f"Created Stripe product: {reelin_pro.id}")
            return self._product_id

        except StripeError as e:
            logger.exception(f"Failed to ensure Stripe product: {e}")
            raise

    async def _get_or_create_price(
        self,
        amount_cents: int,
        currency: str,
        interval: str,
        plan_type: str,
    ) -> str:
        """
        Get existing Stripe price or create new one for given amount/interval.

        Prices are cached by (amount, interval) to avoid repeated API calls.
        When admin changes price in settings, new Stripe price is created.
        """
        cache_key = (amount_cents, interval)

        # Check cache first
        if cache_key in _stripe_price_cache:
            return _stripe_price_cache[cache_key]

        product_id = await self._ensure_product()

        try:
            # Search for existing price with this amount and interval
            prices = stripe.Price.list(product=product_id, active=True, limit=100)
            for price in prices.data:
                if (
                    price.recurring
                    and price.recurring.interval == interval
                    and price.unit_amount == amount_cents
                ):
                    _stripe_price_cache[cache_key] = price.id
                    return price.id

            # Create new price
            new_price = stripe.Price.create(
                product=product_id,
                unit_amount=amount_cents,
                currency=currency,
                recurring={"interval": interval},
                metadata={"plan_type": plan_type},
            )
            _stripe_price_cache[cache_key] = new_price.id
            logger.info(f"Created Stripe price for {plan_type} (€{amount_cents/100}): {new_price.id}")
            return new_price.id

        except StripeError as e:
            logger.exception(f"Failed to get/create Stripe price: {e}")
            raise

    async def get_or_create_customer(
        self,
        user_id: int,
        email: str,
        name: Optional[str] = None,
        existing_customer_id: Optional[str] = None,
    ) -> str:
        """
        Get existing Stripe customer or create new one.

        Args:
            user_id: ReelIn user ID
            email: User email
            name: User display name
            existing_customer_id: Existing Stripe customer ID if known

        Returns:
            Stripe customer ID
        """
        if not self._is_configured():
            raise ValueError("Stripe is not configured")

        # Try to retrieve existing customer
        if existing_customer_id:
            try:
                customer = stripe.Customer.retrieve(existing_customer_id)
                if not customer.deleted:
                    return customer.id
            except stripe.error.InvalidRequestError:
                logger.warning(f"Customer {existing_customer_id} not found, creating new")

        # Search by email
        try:
            customers = stripe.Customer.list(email=email, limit=1)
            if customers.data:
                # Update metadata if needed
                customer = customers.data[0]
                if customer.metadata.get("reelin_user_id") != str(user_id):
                    stripe.Customer.modify(
                        customer.id,
                        metadata={"reelin_user_id": str(user_id)},
                    )
                return customer.id
        except StripeError as e:
            logger.warning(f"Error searching for customer: {e}")

        # Create new customer
        try:
            customer = stripe.Customer.create(
                email=email,
                name=name,
                metadata={"reelin_user_id": str(user_id)},
            )
            logger.info(f"Created Stripe customer {customer.id} for user {user_id}")
            return customer.id
        except StripeError as e:
            logger.exception(f"Failed to create Stripe customer: {e}")
            raise

    async def create_checkout_session(
        self,
        user_id: int,
        email: str,
        plan_type: str,
        price_eur: float,
        customer_id: Optional[str] = None,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
        trial_days: Optional[int] = None,
    ) -> Tuple[str, str]:
        """
        Create a Stripe Checkout session for Pro subscription.

        Args:
            user_id: ReelIn user ID
            email: User email
            plan_type: 'monthly' or 'yearly'
            price_eur: Price in EUR (fetched from admin settings)
            customer_id: Existing Stripe customer ID
            success_url: URL to redirect on success
            cancel_url: URL to redirect on cancel
            trial_days: Number of trial days (0 or None = no trial)

        Returns:
            Tuple of (checkout_url, session_id)
        """
        if not self._is_configured():
            raise ValueError("Stripe is not configured")

        if plan_type not in DEFAULT_PRICES:
            raise ValueError(f"Invalid plan type: {plan_type}")

        # Get interval from plan type
        interval = DEFAULT_PRICES[plan_type]["interval"]
        currency = DEFAULT_PRICES[plan_type]["currency"]

        # Convert EUR price to cents
        amount_cents = int(price_eur * 100)

        # Get or create Stripe price for this amount
        price_id = await self._get_or_create_price(
            amount_cents=amount_cents,
            currency=currency,
            interval=interval,
            plan_type=plan_type,
        )

        # Get or create customer
        if not customer_id:
            customer_id = await self.get_or_create_customer(user_id, email)

        # Default URLs
        frontend_url = settings.frontend_url
        if not success_url:
            success_url = f"{frontend_url}/pro/success?session_id={{CHECKOUT_SESSION_ID}}"
        if not cancel_url:
            cancel_url = f"{frontend_url}/pro/cancel"

        try:
            # Build subscription_data with optional trial
            subscription_data = {
                "metadata": {
                    "reelin_user_id": str(user_id),
                    "plan_type": plan_type,
                }
            }

            # Add trial if specified and > 0
            if trial_days and trial_days > 0:
                subscription_data["trial_period_days"] = trial_days

            session = stripe.checkout.Session.create(
                customer=customer_id,
                mode="subscription",
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=success_url,
                cancel_url=cancel_url,
                subscription_data=subscription_data,
                metadata={
                    "reelin_user_id": str(user_id),
                    "plan_type": plan_type,
                },
            )
            logger.info(f"Created checkout session {session.id} for user {user_id} (€{price_eur}, trial_days={trial_days})")
            return session.url, session.id

        except StripeError as e:
            logger.exception(f"Failed to create checkout session: {e}")
            raise

    async def create_portal_session(
        self,
        customer_id: str,
        return_url: Optional[str] = None,
    ) -> str:
        """
        Create a Stripe Customer Portal session for subscription management.

        Args:
            customer_id: Stripe customer ID
            return_url: URL to return to after portal session

        Returns:
            Portal session URL
        """
        if not self._is_configured():
            raise ValueError("Stripe is not configured")

        if not return_url:
            return_url = f"{settings.frontend_url}/profile"

        try:
            session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url,
            )
            return session.url

        except StripeError as e:
            logger.exception(f"Failed to create portal session: {e}")
            raise

    async def cancel_subscription(
        self,
        subscription_id: str,
        cancel_immediately: bool = False,
    ) -> dict:
        """
        Cancel a subscription.

        Args:
            subscription_id: Stripe subscription ID
            cancel_immediately: If True, cancel immediately; otherwise at period end

        Returns:
            Updated subscription data
        """
        if not self._is_configured():
            raise ValueError("Stripe is not configured")

        try:
            if cancel_immediately:
                subscription = stripe.Subscription.delete(subscription_id)
            else:
                subscription = stripe.Subscription.modify(
                    subscription_id,
                    cancel_at_period_end=True,
                )
            logger.info(f"Cancelled subscription {subscription_id}, immediately={cancel_immediately}")
            return {
                "id": subscription.id,
                "status": subscription.status,
                "cancel_at_period_end": subscription.cancel_at_period_end,
                "current_period_end": datetime.fromtimestamp(
                    subscription.current_period_end, tz=timezone.utc
                ),
            }

        except StripeError as e:
            logger.exception(f"Failed to cancel subscription: {e}")
            raise

    async def resume_subscription(self, subscription_id: str) -> dict:
        """
        Resume a cancelled subscription (if still in current period).

        Args:
            subscription_id: Stripe subscription ID

        Returns:
            Updated subscription data
        """
        if not self._is_configured():
            raise ValueError("Stripe is not configured")

        try:
            subscription = stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=False,
            )
            logger.info(f"Resumed subscription {subscription_id}")
            return {
                "id": subscription.id,
                "status": subscription.status,
                "cancel_at_period_end": subscription.cancel_at_period_end,
            }

        except StripeError as e:
            logger.exception(f"Failed to resume subscription: {e}")
            raise

    async def get_subscription(self, subscription_id: str) -> Optional[dict]:
        """
        Get subscription details from Stripe.

        Args:
            subscription_id: Stripe subscription ID

        Returns:
            Subscription data or None if not found
        """
        if not self._is_configured():
            return None

        try:
            subscription = stripe.Subscription.retrieve(subscription_id)
            return {
                "id": subscription.id,
                "status": subscription.status,
                "plan_type": subscription.metadata.get("plan_type"),
                "current_period_start": datetime.fromtimestamp(
                    subscription.current_period_start, tz=timezone.utc
                ),
                "current_period_end": datetime.fromtimestamp(
                    subscription.current_period_end, tz=timezone.utc
                ),
                "cancel_at_period_end": subscription.cancel_at_period_end,
                "canceled_at": (
                    datetime.fromtimestamp(subscription.canceled_at, tz=timezone.utc)
                    if subscription.canceled_at
                    else None
                ),
            }

        except stripe.error.InvalidRequestError:
            return None
        except StripeError as e:
            logger.exception(f"Failed to get subscription: {e}")
            return None

    async def has_active_subscription(self, customer_id: str) -> bool:
        """
        Check if a customer has any active subscriptions in Stripe.

        This is a safeguard to prevent duplicate subscriptions when webhooks fail.

        Args:
            customer_id: Stripe customer ID

        Returns:
            True if customer has active subscriptions
        """
        if not self._is_configured():
            return False

        try:
            subscriptions = stripe.Subscription.list(
                customer=customer_id,
                status="active",
                limit=1,
            )
            return len(subscriptions.data) > 0

        except StripeError as e:
            logger.exception(f"Failed to check active subscriptions: {e}")
            # Return False to allow checkout attempt (Stripe will prevent duplicates)
            return False

    def get_plans(self) -> list:
        """
        Get available subscription plans (default values).

        Note: The API endpoint fetches actual prices from admin settings.
        This method returns default fallback values.

        Returns:
            List of plan details
        """
        # Default prices - actual prices come from admin settings
        return [
            {
                "id": "monthly",
                "name": "Monthly",
                "price": DEFAULT_PRICES["monthly"]["amount"] / 100,
                "currency": "EUR",
                "interval": "month",
            },
            {
                "id": "yearly",
                "name": "Yearly",
                "price": DEFAULT_PRICES["yearly"]["amount"] / 100,
                "currency": "EUR",
                "interval": "year",
                "description": "Save €15.89/year",
            },
        ]


# Singleton instance
stripe_subscription_service = StripeSubscriptionService()
