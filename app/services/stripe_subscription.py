"""Stripe Subscription integration service for ReelIn Pro.

This module handles all Stripe-related operations for Pro subscriptions,
including checkout sessions, customer portal, and subscription management.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

import stripe
from stripe.error import StripeError

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Stripe Product/Price IDs (will be created in Stripe Dashboard)
# These should match the prices you create in Stripe
#
# PRICING UPDATE (Story 7.5):
# - Monthly: €2.99 → €4.99 (+67%)
# - Yearly: €19.99 → €39.99 (+100%, 4 months free)
# Existing subscribers keep their old price via Stripe's grandfathering.
STRIPE_PRICES = {
    "monthly": {
        "price_id": None,  # Will be set from environment or created
        "amount": 499,  # €4.99 in cents
        "currency": "eur",
        "interval": "month",
    },
    "yearly": {
        "price_id": None,  # Will be set from environment or created
        "amount": 3999,  # €39.99 in cents
        "currency": "eur",
        "interval": "year",
    },
}


class StripeSubscriptionService:
    """Service for Stripe subscription operations."""

    def __init__(self):
        """Initialize Stripe with API key."""
        stripe.api_key = settings.stripe_secret_key
        self._product_id: Optional[str] = None
        self._prices_initialized = False

    def _is_configured(self) -> bool:
        """Check if Stripe is properly configured."""
        return bool(settings.stripe_secret_key)

    async def _ensure_product_and_prices(self) -> None:
        """Ensure the ReelIn Pro product and prices exist in Stripe."""
        if self._prices_initialized:
            return

        if not self._is_configured():
            logger.warning("Stripe not configured, skipping product/price setup")
            return

        try:
            # Search for existing product
            products = stripe.Product.list(active=True, limit=100)
            reelin_pro = None
            for product in products.data:
                if product.metadata.get("reelin_product") == "pro":
                    reelin_pro = product
                    break

            # Create product if not exists
            if not reelin_pro:
                reelin_pro = stripe.Product.create(
                    name="ReelIn Pro",
                    description="Premium features for ReelIn fishing competition app",
                    metadata={"reelin_product": "pro"},
                )
                logger.info(f"Created Stripe product: {reelin_pro.id}")

            self._product_id = reelin_pro.id

            # Get or create prices
            prices = stripe.Price.list(product=reelin_pro.id, active=True, limit=100)

            for plan_type, plan_config in STRIPE_PRICES.items():
                existing_price = None
                for price in prices.data:
                    if (
                        price.recurring
                        and price.recurring.interval == plan_config["interval"]
                        and price.unit_amount == plan_config["amount"]
                    ):
                        existing_price = price
                        break

                if existing_price:
                    STRIPE_PRICES[plan_type]["price_id"] = existing_price.id
                else:
                    # Create the price
                    new_price = stripe.Price.create(
                        product=reelin_pro.id,
                        unit_amount=plan_config["amount"],
                        currency=plan_config["currency"],
                        recurring={"interval": plan_config["interval"]},
                        metadata={"plan_type": plan_type},
                    )
                    STRIPE_PRICES[plan_type]["price_id"] = new_price.id
                    logger.info(f"Created Stripe price for {plan_type}: {new_price.id}")

            self._prices_initialized = True
            logger.info("Stripe product and prices initialized")

        except StripeError as e:
            logger.error(f"Failed to initialize Stripe products/prices: {e}")
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
            logger.error(f"Failed to create Stripe customer: {e}")
            raise

    async def create_checkout_session(
        self,
        user_id: int,
        email: str,
        plan_type: str,
        customer_id: Optional[str] = None,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Create a Stripe Checkout session for Pro subscription.

        Args:
            user_id: ReelIn user ID
            email: User email
            plan_type: 'monthly' or 'yearly'
            customer_id: Existing Stripe customer ID
            success_url: URL to redirect on success
            cancel_url: URL to redirect on cancel

        Returns:
            Tuple of (checkout_url, session_id)
        """
        if not self._is_configured():
            raise ValueError("Stripe is not configured")

        await self._ensure_product_and_prices()

        if plan_type not in STRIPE_PRICES:
            raise ValueError(f"Invalid plan type: {plan_type}")

        price_id = STRIPE_PRICES[plan_type]["price_id"]
        if not price_id:
            raise ValueError(f"Price not configured for plan: {plan_type}")

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
            session = stripe.checkout.Session.create(
                customer=customer_id,
                mode="subscription",
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=success_url,
                cancel_url=cancel_url,
                subscription_data={
                    "metadata": {
                        "reelin_user_id": str(user_id),
                        "plan_type": plan_type,
                    }
                },
                metadata={
                    "reelin_user_id": str(user_id),
                    "plan_type": plan_type,
                },
            )
            logger.info(f"Created checkout session {session.id} for user {user_id}")
            return session.url, session.id

        except StripeError as e:
            logger.error(f"Failed to create checkout session: {e}")
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
            logger.error(f"Failed to create portal session: {e}")
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
            logger.error(f"Failed to cancel subscription: {e}")
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
            logger.error(f"Failed to resume subscription: {e}")
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
            logger.error(f"Failed to get subscription: {e}")
            return None

    def get_plans(self) -> list:
        """
        Get available subscription plans.

        Returns:
            List of plan details
        """
        # Calculate yearly savings: €4.99 * 12 = €59.88 vs €39.99 = €19.89 saved (33%)
        return [
            {
                "id": "monthly",
                "name": "Monthly",
                "price": 4.99,
                "currency": "EUR",
                "interval": "month",
                "stripe_price_id": STRIPE_PRICES["monthly"].get("price_id"),
            },
            {
                "id": "yearly",
                "name": "Yearly",
                "price": 39.99,
                "currency": "EUR",
                "interval": "year",
                "description": "Save €19.89/year",
                "stripe_price_id": STRIPE_PRICES["yearly"].get("price_id"),
            },
        ]


# Singleton instance
stripe_subscription_service = StripeSubscriptionService()
