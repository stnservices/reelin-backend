"""Email service for sending transactional emails via AWS SES SMTP."""

import logging
import smtplib
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import get_settings

logger = logging.getLogger(__name__)

# Initialize Jinja2 template environment
TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "email"
jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)


class EmailService:
    """Service for sending emails via AWS SES SMTP."""

    def __init__(self):
        self.settings = get_settings()
        self._validate_config()

    def _validate_config(self) -> bool:
        """Check if email configuration is complete."""
        if not self.settings.email_host:
            logger.warning("EMAIL_HOST not configured, email sending disabled")
            return False
        if not self.settings.email_host_user:
            logger.warning("EMAIL_HOST_USER not configured, email sending disabled")
            return False
        if not self.settings.email_host_password:
            logger.warning("EMAIL_HOST_PASSWORD not configured, email sending disabled")
            return False
        return True

    def is_configured(self) -> bool:
        """Check if email service is properly configured."""
        return bool(
            self.settings.email_host
            and self.settings.email_host_user
            and self.settings.email_host_password
        )

    def _create_smtp_connection(self) -> smtplib.SMTP:
        """Create and return an SMTP connection."""
        smtp = smtplib.SMTP(
            self.settings.email_host,
            self.settings.email_port,
            timeout=self.settings.email_timeout,
        )

        if self.settings.email_use_tls:
            context = ssl.create_default_context()
            smtp.starttls(context=context)

        smtp.login(
            self.settings.email_host_user,
            self.settings.email_host_password,
        )

        return smtp

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
    ) -> bool:
        """
        Send an email with HTML and optional plain text content.

        Args:
            to_email: Recipient email address
            subject: Email subject line
            html_content: HTML body content
            text_content: Plain text fallback (optional)

        Returns:
            True if email was sent successfully, False otherwise
        """
        if not self.is_configured():
            logger.warning(f"Email not configured, skipping email to {to_email}")
            return False

        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.settings.default_from_email
        msg["To"] = to_email

        # Add plain text part (fallback)
        if text_content:
            part1 = MIMEText(text_content, "plain", "utf-8")
            msg.attach(part1)

        # Add HTML part
        part2 = MIMEText(html_content, "html", "utf-8")
        msg.attach(part2)

        # Send with retry logic
        last_error = None
        for attempt in range(self.settings.email_max_retries):
            try:
                with self._create_smtp_connection() as smtp:
                    smtp.send_message(msg)
                    logger.info(f"Email sent successfully to {to_email}: {subject}")
                    return True
            except smtplib.SMTPException as e:
                last_error = e
                logger.warning(
                    f"SMTP error sending email (attempt {attempt + 1}/{self.settings.email_max_retries}): {e}"
                )
                if attempt < self.settings.email_max_retries - 1:
                    time.sleep(self.settings.email_retry_backoff_seconds * (attempt + 1))
            except Exception as e:
                last_error = e
                logger.error(f"Unexpected error sending email: {e}")
                break

        logger.error(f"Failed to send email to {to_email} after {self.settings.email_max_retries} attempts: {last_error}")
        return False

    def render_template(self, template_name: str, **context) -> tuple[str, str]:
        """
        Render an email template with the given context.

        Args:
            template_name: Name of the template file (without extension)
            **context: Template context variables

        Returns:
            Tuple of (html_content, text_content)
        """
        # Add common context
        context.setdefault("site_name", self.settings.site_name)
        context.setdefault("frontend_url", self.settings.frontend_url)

        # Render HTML template
        html_template = jinja_env.get_template(f"{template_name}.html")
        html_content = html_template.render(**context)

        # Try to render text template, fallback to simple text
        try:
            text_template = jinja_env.get_template(f"{template_name}.txt")
            text_content = text_template.render(**context)
        except Exception:
            # Generate simple text from context
            text_content = self._generate_text_fallback(template_name, context)

        return html_content, text_content

    def _generate_text_fallback(self, template_name: str, context: dict) -> str:
        """Generate a simple text fallback for email templates."""
        site_name = context.get("site_name", "ReelIn")

        if template_name == "activation":
            return (
                f"Activate your {site_name} account\n\n"
                f"Hi {context.get('first_name', 'there')},\n\n"
                f"Please click the following link to activate your account:\n"
                f"{context.get('activation_url', '')}\n\n"
                f"This link will expire in 24 hours.\n\n"
                f"If you didn't create an account, please ignore this email.\n\n"
                f"Thanks,\nThe {site_name} team"
            )
        elif template_name == "password_reset":
            return (
                f"Reset your {site_name} password\n\n"
                f"Hi {context.get('first_name', 'there')},\n\n"
                f"You requested a password reset. Click the link below to choose a new password:\n"
                f"{context.get('reset_url', '')}\n\n"
                f"This link will expire in 1 hour.\n\n"
                f"If you didn't request this, please ignore this email.\n\n"
                f"Thanks,\nThe {site_name} team"
            )
        elif template_name == "account_deleted":
            return (
                f"Your {site_name} account has been deleted\n\n"
                f"Hi {context.get('first_name', 'there')},\n\n"
                f"Your {site_name} account has been permanently deleted as requested. "
                f"All your personal data has been anonymized in accordance with GDPR regulations.\n\n"
                f"This action is irreversible. If you wish to use {site_name} again in the future, "
                f"you are welcome to create a new account.\n\n"
                f"We're sorry to see you go. Thank you for being part of {site_name}.\n\n"
                f"The {site_name} team"
            )
        else:
            return f"Email from {site_name}"

    def send_activation_email(
        self,
        to_email: str,
        first_name: str,
        activation_token: str,
    ) -> bool:
        """
        Send an account activation email.

        Args:
            to_email: Recipient email address
            first_name: User's first name
            activation_token: JWT activation token

        Returns:
            True if sent successfully, False otherwise
        """
        activation_url = f"{self.settings.frontend_url}/activate/{activation_token}"

        html_content, text_content = self.render_template(
            "activation",
            first_name=first_name,
            activation_url=activation_url,
            activation_token=activation_token,
        )

        return self.send_email(
            to_email=to_email,
            subject=f"Activate your {self.settings.site_name} account",
            html_content=html_content,
            text_content=text_content,
        )

    def send_account_deleted_email(
        self,
        to_email: str,
        first_name: str,
    ) -> bool:
        """
        Send notification that user's account has been permanently deleted.

        Must be called BEFORE the email is anonymized.
        """
        html_content, text_content = self.render_template(
            "account_deleted",
            first_name=first_name,
        )

        return self.send_email(
            to_email=to_email,
            subject=f"Your {self.settings.site_name} account has been deleted",
            html_content=html_content,
            text_content=text_content,
        )

    def send_password_reset_email(
        self,
        to_email: str,
        first_name: str,
        reset_token: str,
    ) -> bool:
        """
        Send a password reset email.

        Args:
            to_email: Recipient email address
            first_name: User's first name
            reset_token: JWT password reset token

        Returns:
            True if sent successfully, False otherwise
        """
        reset_url = f"{self.settings.frontend_url}/password/reset/{reset_token}"

        html_content, text_content = self.render_template(
            "password_reset",
            first_name=first_name,
            reset_url=reset_url,
            reset_token=reset_token,
        )

        return self.send_email(
            to_email=to_email,
            subject=f"Reset your {self.settings.site_name} password",
            html_content=html_content,
            text_content=text_content,
        )


# Singleton instance
_email_service: Optional[EmailService] = None


def get_email_service() -> EmailService:
    """Get the email service singleton instance."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
