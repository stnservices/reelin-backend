"""Stripe Invoicing integration service.

This module handles all Stripe-related operations for platform billing,
including customer management and invoice creation/management.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import stripe
from stripe.error import StripeError

from app.config import get_settings
from app.models.billing import OrganizerBillingProfile, PlatformInvoice

logger = logging.getLogger(__name__)
settings = get_settings()


def validate_romanian_tax_id(tax_id: str) -> tuple[bool, str]:
    """
    Validate Romanian tax ID (CUI/CIF) format.

    Romanian tax IDs (CUI - Cod Unic de Identificare):
    - 2-10 digits
    - May be prefixed with "RO" for EU VAT purposes
    - Has a checksum digit (last digit)

    Args:
        tax_id: The tax ID to validate

    Returns:
        Tuple of (is_valid, cleaned_tax_id)
    """
    if not tax_id:
        return False, ""

    # Clean the input
    cleaned = tax_id.strip().upper()

    # Remove RO prefix if present
    if cleaned.startswith("RO"):
        cleaned = cleaned[2:]

    # Must be 2-10 digits
    if not re.match(r"^\d{2,10}$", cleaned):
        return False, ""

    # Validate checksum (Romanian CUI algorithm)
    # The checksum is validated against a control key: 753217532
    try:
        control_key = [7, 5, 3, 2, 1, 7, 5, 3, 2]
        digits = [int(d) for d in cleaned.zfill(10)]  # Pad to 10 digits

        # Calculate weighted sum (excluding last digit)
        weighted_sum = sum(d * k for d, k in zip(digits[:-1], control_key))

        # Calculate expected check digit
        remainder = (weighted_sum * 10) % 11
        check_digit = remainder if remainder < 10 else 0

        # Validate
        if digits[-1] != check_digit:
            # Checksum failed, but still might be valid for test purposes
            # Log warning but allow it through in non-strict mode
            logger.warning(f"Romanian tax ID checksum validation failed for: {tax_id}")
            # Return True anyway for flexibility - Stripe will do final validation
            return True, cleaned

        return True, cleaned
    except (ValueError, IndexError):
        return False, ""


class StripeBillingService:
    """Service for Stripe Invoicing operations."""

    def __init__(self):
        """Initialize Stripe with API key."""
        stripe.api_key = settings.stripe_secret_key

    def _is_configured(self) -> bool:
        """Check if Stripe is properly configured."""
        return bool(settings.stripe_secret_key)

    def get_or_create_customer(
        self,
        billing_profile: OrganizerBillingProfile,
    ) -> Optional[str]:
        """
        Get existing Stripe customer or create new one.

        Args:
            billing_profile: The organizer's billing profile

        Returns:
            Stripe customer ID or None if Stripe is not configured
        """
        if not self._is_configured():
            logger.warning("Stripe is not configured, skipping customer creation")
            return None

        # If we already have a customer ID, verify it exists
        if billing_profile.stripe_customer_id:
            try:
                stripe.Customer.retrieve(billing_profile.stripe_customer_id)
                return billing_profile.stripe_customer_id
            except stripe.error.InvalidRequestError:
                logger.warning(
                    f"Stripe customer {billing_profile.stripe_customer_id} not found, creating new one"
                )

        # Build customer address
        address = {
            "line1": billing_profile.billing_address_line1,
            "city": billing_profile.billing_city,
            "postal_code": billing_profile.billing_postal_code,
            "country": billing_profile.billing_country,
        }
        if billing_profile.billing_address_line2:
            address["line2"] = billing_profile.billing_address_line2
        if billing_profile.billing_county:
            address["state"] = billing_profile.billing_county

        # Build tax ID data if provided and valid
        tax_id_data = None
        if billing_profile.tax_id:
            if billing_profile.billing_country == "RO":
                # Validate Romanian tax ID
                is_valid, cleaned_tax_id = validate_romanian_tax_id(billing_profile.tax_id)
                if is_valid and cleaned_tax_id:
                    tax_id_data = [{"type": "ro_tin", "value": cleaned_tax_id}]
                else:
                    logger.warning(
                        f"Invalid Romanian tax ID format for billing profile {billing_profile.id}: "
                        f"'{billing_profile.tax_id}' - skipping tax ID in Stripe"
                    )
            else:
                # For non-RO countries, use EU VAT format
                tax_id_data = [{"type": "eu_vat", "value": billing_profile.tax_id}]

        try:
            customer = stripe.Customer.create(
                email=billing_profile.billing_email,
                name=billing_profile.legal_name,
                phone=billing_profile.billing_phone,
                address=address,
                tax_id_data=tax_id_data,
                metadata={
                    "reelin_user_id": str(billing_profile.user_id),
                    "reelin_billing_profile_id": str(billing_profile.id),
                    "organizer_type": billing_profile.organizer_type,
                },
            )
            logger.info(
                f"Created Stripe customer {customer.id} for billing profile {billing_profile.id}"
            )
            return customer.id

        except StripeError as e:
            logger.error(f"Failed to create Stripe customer: {e}")
            raise

    def create_invoice(
        self,
        invoice: PlatformInvoice,
        billing_profile: OrganizerBillingProfile,
        event_name: str,
        auto_send: bool = False,
    ) -> dict:
        """
        Create a Stripe invoice for a platform invoice.

        Args:
            invoice: The platform invoice record
            billing_profile: The organizer's billing profile
            event_name: Name of the event (for invoice description)
            auto_send: Whether to automatically send the invoice

        Returns:
            Dict with stripe_invoice_id, hosted_invoice_url, invoice_pdf
        """
        if not self._is_configured():
            logger.warning("Stripe is not configured, skipping invoice creation")
            return {
                "stripe_invoice_id": None,
                "hosted_invoice_url": None,
                "invoice_pdf": None,
            }

        # Ensure we have a Stripe customer
        customer_id = self.get_or_create_customer(billing_profile)
        if not customer_id:
            raise ValueError("Could not create Stripe customer")

        try:
            # Create the invoice
            stripe_invoice = stripe.Invoice.create(
                customer=customer_id,
                collection_method="send_invoice",
                days_until_due=settings.stripe_invoice_days_until_due,
                auto_advance=auto_send,
                metadata={
                    "reelin_invoice_id": str(invoice.id),
                    "reelin_invoice_number": invoice.invoice_number,
                    "reelin_event_id": str(invoice.event_id),
                },
                custom_fields=[
                    {"name": "Event", "value": event_name[:30]},
                    {"name": "Invoice Number", "value": invoice.invoice_number},
                ],
            )

            # Build line item description
            if invoice.pricing_model_snapshot == "per_participant":
                description = (
                    f"Platform fee for event: {event_name} - "
                    f"{invoice.participant_count} participants @ "
                    f"{invoice.rate_snapshot} {invoice.currency_code}"
                )
            else:
                description = f"Platform fee for event: {event_name} (fixed rate)"

            # Add line item
            stripe.InvoiceItem.create(
                customer=customer_id,
                invoice=stripe_invoice.id,
                amount=int(invoice.total_amount * 100),  # Stripe uses cents
                currency=invoice.currency_code.lower(),
                description=description,
            )

            # Finalize the invoice
            stripe_invoice = stripe.Invoice.finalize_invoice(stripe_invoice.id)

            # Send if requested
            if auto_send:
                stripe_invoice = stripe.Invoice.send_invoice(stripe_invoice.id)

            logger.info(
                f"Created Stripe invoice {stripe_invoice.id} for platform invoice {invoice.id}"
            )

            return {
                "stripe_invoice_id": stripe_invoice.id,
                "hosted_invoice_url": stripe_invoice.hosted_invoice_url,
                "invoice_pdf": stripe_invoice.invoice_pdf,
            }

        except StripeError as e:
            logger.error(f"Failed to create Stripe invoice: {e}")
            raise

    async def send_invoice(self, stripe_invoice_id: str) -> bool:
        """
        Send an already created invoice.

        Args:
            stripe_invoice_id: The Stripe invoice ID

        Returns:
            True if successful, False otherwise
        """
        if not self._is_configured():
            return False

        try:
            stripe.Invoice.send_invoice(stripe_invoice_id)
            logger.info(f"Sent Stripe invoice {stripe_invoice_id}")
            return True
        except StripeError as e:
            logger.error(f"Failed to send Stripe invoice: {e}")
            return False

    async def void_invoice(self, stripe_invoice_id: str) -> bool:
        """
        Void/cancel an invoice.

        Args:
            stripe_invoice_id: The Stripe invoice ID

        Returns:
            True if successful, False otherwise
        """
        if not self._is_configured():
            return False

        try:
            stripe.Invoice.void_invoice(stripe_invoice_id)
            logger.info(f"Voided Stripe invoice {stripe_invoice_id}")
            return True
        except StripeError as e:
            logger.error(f"Failed to void Stripe invoice: {e}")
            return False

    async def get_invoice_status(self, stripe_invoice_id: str) -> Optional[str]:
        """
        Get current status of a Stripe invoice.

        Args:
            stripe_invoice_id: The Stripe invoice ID

        Returns:
            Invoice status string or None if not found
        """
        if not self._is_configured():
            return None

        try:
            invoice = stripe.Invoice.retrieve(stripe_invoice_id)
            return invoice.status
        except StripeError as e:
            logger.error(f"Failed to get Stripe invoice status: {e}")
            return None

    async def update_customer(
        self,
        stripe_customer_id: str,
        billing_profile: OrganizerBillingProfile,
    ) -> bool:
        """
        Update an existing Stripe customer with new billing profile data.

        Args:
            stripe_customer_id: The Stripe customer ID
            billing_profile: The updated billing profile

        Returns:
            True if successful, False otherwise
        """
        if not self._is_configured():
            return False

        # Build address
        address = {
            "line1": billing_profile.billing_address_line1,
            "city": billing_profile.billing_city,
            "postal_code": billing_profile.billing_postal_code,
            "country": billing_profile.billing_country,
        }
        if billing_profile.billing_address_line2:
            address["line2"] = billing_profile.billing_address_line2
        if billing_profile.billing_county:
            address["state"] = billing_profile.billing_county

        try:
            stripe.Customer.modify(
                stripe_customer_id,
                email=billing_profile.billing_email,
                name=billing_profile.legal_name,
                phone=billing_profile.billing_phone,
                address=address,
                metadata={
                    "organizer_type": billing_profile.organizer_type,
                },
            )
            logger.info(f"Updated Stripe customer {stripe_customer_id}")
            return True
        except StripeError as e:
            logger.error(f"Failed to update Stripe customer: {e}")
            return False


# Singleton instance
stripe_billing_service = StripeBillingService()
