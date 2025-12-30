"""Account Deletion service for GDPR compliance and app store requirements.

This module handles account deletion with a configurable grace period,
allowing users to recover their accounts before permanent anonymization.

Two-stage deletion process:
1. Schedule deletion (soft delete) - user can recover within grace period
2. Permanent anonymization - after grace period expires, data is scrambled
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update, delete, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserAccount, UserProfile
from app.models.pro import ProGrant, ProSubscription, ProAuditLog, ProAction, ProSettings
from app.models.social_account import SocialAccount
from app.models.notification import UserDeviceToken
from app.models.event import Event, EventStatus

logger = logging.getLogger(__name__)

# Default grace period if setting not found
DEFAULT_GRACE_PERIOD_DAYS = 30


class AccountPendingDeletionError(Exception):
    """Raised when user tries to login with account pending deletion."""

    def __init__(self, days_remaining: int, deletion_scheduled_at: datetime, permanent_deletion_at: datetime):
        self.days_remaining = days_remaining
        self.deletion_scheduled_at = deletion_scheduled_at
        self.permanent_deletion_at = permanent_deletion_at
        super().__init__(f"Account scheduled for deletion. {days_remaining} days remaining to recover.")


class AccountDeletionError(Exception):
    """Raised when account deletion is not allowed."""
    pass


class AccountDeletionService:
    """Service for account deletion with grace period support."""

    async def get_grace_period_days(self, db: AsyncSession) -> int:
        """Get the grace period from settings."""
        query = select(ProSettings).where(ProSettings.key == "account_deletion_grace_period_days")
        result = await db.execute(query)
        setting = result.scalar_one_or_none()

        if setting:
            try:
                return int(setting.value)
            except ValueError:
                pass

        return DEFAULT_GRACE_PERIOD_DAYS

    async def check_deletion_constraints(self, user_id: int, db: AsyncSession) -> list[str]:
        """
        Check if user has any constraints preventing deletion.

        Returns list of constraint messages if any exist.
        """
        constraints = []

        # Check if user is organizer of ongoing events
        ongoing_events_query = select(Event).where(
            Event.created_by_id == user_id,
            Event.status.in_([EventStatus.PUBLISHED.value, EventStatus.ONGOING.value])
        )
        result = await db.execute(ongoing_events_query)
        ongoing_events = result.scalars().all()

        if ongoing_events:
            event_names = ", ".join([e.name for e in ongoing_events[:3]])
            constraints.append(f"You are the organizer of active events: {event_names}")

        # TODO: Check if user is club owner with members (when clubs feature is complete)

        return constraints

    async def schedule_deletion(
        self,
        user_id: int,
        password: Optional[str],
        db: AsyncSession
    ) -> dict:
        """
        Stage 1: Schedule account for deletion with grace period.

        Args:
            user_id: The user's ID
            password: Password for verification (None for OAuth-only accounts)
            db: Database session

        Returns:
            Dict with deletion schedule information

        Raises:
            AccountDeletionError: If deletion is not allowed
        """
        # Get user
        query = select(UserAccount).where(UserAccount.id == user_id)
        result = await db.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            raise AccountDeletionError("User not found")

        # Check if already scheduled for deletion
        if user.deletion_scheduled_at:
            raise AccountDeletionError("Account is already scheduled for deletion")

        # Verify password for password-based accounts
        if user.has_password and password:
            from app.core.security import verify_password
            if not verify_password(password, user.password_hash):
                raise AccountDeletionError("Invalid password")
        elif user.has_password and not password:
            raise AccountDeletionError("Password required for verification")

        # Check constraints
        constraints = await self.check_deletion_constraints(user_id, db)
        if constraints:
            raise AccountDeletionError("; ".join(constraints))

        # Get grace period
        grace_period_days = await self.get_grace_period_days(db)
        now = datetime.now(timezone.utc)
        permanent_deletion_at = now + timedelta(days=grace_period_days)

        # Schedule deletion
        user.deletion_scheduled_at = now
        user.is_active = False  # Prevents login

        # Delete device tokens (stops push notifications)
        await db.execute(
            delete(UserDeviceToken).where(UserDeviceToken.user_id == user_id)
        )

        # Log the action
        audit_log = ProAuditLog(
            admin_id=user_id,  # User deleting their own account
            user_id=user_id,
            action="account_deletion_scheduled",
            details={
                "grace_period_days": grace_period_days,
                "permanent_deletion_at": permanent_deletion_at.isoformat()
            },
            reason="User requested account deletion"
        )
        db.add(audit_log)

        await db.commit()

        logger.info(f"Account deletion scheduled for user {user_id}, permanent deletion at {permanent_deletion_at}")

        return {
            "message": "Account scheduled for deletion",
            "deletion_scheduled_at": now,
            "permanent_deletion_at": permanent_deletion_at,
            "grace_period_days": grace_period_days,
            "can_recover": True
        }

    async def check_pending_deletion(
        self,
        user: UserAccount,
        db: AsyncSession
    ) -> Optional[dict]:
        """
        Check if user's account is pending deletion.

        Returns dict with deletion info if pending, None otherwise.
        """
        if not user.deletion_scheduled_at:
            return None

        grace_period_days = await self.get_grace_period_days(db)
        permanent_deletion_at = user.deletion_scheduled_at + timedelta(days=grace_period_days)
        now = datetime.now(timezone.utc)

        if now >= permanent_deletion_at:
            # Grace period expired - should have been anonymized
            # This shouldn't happen if background job runs correctly
            return None

        days_remaining = (permanent_deletion_at - now).days

        return {
            "deletion_scheduled_at": user.deletion_scheduled_at,
            "permanent_deletion_at": permanent_deletion_at,
            "days_remaining": days_remaining,
            "can_recover": True
        }

    async def recover_account(
        self,
        user_id: int,
        db: AsyncSession
    ) -> dict:
        """
        Cancel scheduled deletion and reactivate account.

        Args:
            user_id: The user's ID
            db: Database session

        Returns:
            Dict with recovery confirmation

        Raises:
            AccountDeletionError: If recovery is not possible
        """
        query = select(UserAccount).where(UserAccount.id == user_id)
        result = await db.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            raise AccountDeletionError("User not found")

        if not user.deletion_scheduled_at:
            raise AccountDeletionError("Account is not pending deletion")

        # Check if grace period has expired
        grace_period_days = await self.get_grace_period_days(db)
        permanent_deletion_at = user.deletion_scheduled_at + timedelta(days=grace_period_days)

        if datetime.now(timezone.utc) >= permanent_deletion_at:
            raise AccountDeletionError("Grace period has expired. Account cannot be recovered.")

        # Reactivate account
        user.deletion_scheduled_at = None
        user.is_active = True

        # Log the action
        audit_log = ProAuditLog(
            admin_id=user_id,  # User recovering their own account
            user_id=user_id,
            action="account_deletion_cancelled",
            details={"recovered_by": "user"},
            reason="User cancelled account deletion"
        )
        db.add(audit_log)

        await db.commit()

        logger.info(f"Account deletion cancelled for user {user_id}")

        return {
            "message": "Account recovered successfully",
            "recovered_at": datetime.now(timezone.utc)
        }

    async def admin_force_recover(
        self,
        user_id: int,
        admin_id: int,
        reason: str,
        db: AsyncSession
    ) -> dict:
        """
        Admin force recovery of an account (even within grace period).

        Args:
            user_id: The user's ID to recover
            admin_id: The admin performing the action
            reason: Reason for force recovery
            db: Database session

        Returns:
            Dict with recovery confirmation
        """
        query = select(UserAccount).where(UserAccount.id == user_id)
        result = await db.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            raise AccountDeletionError("User not found")

        if not user.deletion_scheduled_at:
            raise AccountDeletionError("Account is not pending deletion")

        # Reactivate account
        user.deletion_scheduled_at = None
        user.is_active = True

        # Log the action
        audit_log = ProAuditLog(
            admin_id=admin_id,
            user_id=user_id,
            action="account_deletion_force_cancelled",
            details={"recovered_by": "admin", "admin_id": admin_id},
            reason=reason
        )
        db.add(audit_log)

        await db.commit()

        logger.info(f"Account deletion force-cancelled by admin {admin_id} for user {user_id}")

        return {
            "message": "Account recovered by admin",
            "recovered_at": datetime.now(timezone.utc),
            "admin_id": admin_id
        }

    async def permanently_anonymize(
        self,
        user_id: int,
        db: AsyncSession
    ) -> dict:
        """
        Stage 2: Permanently anonymize user data.

        This is irreversible and should only be called by the background job
        after the grace period has expired.

        Args:
            user_id: The user's ID
            db: Database session

        Returns:
            Dict with anonymization confirmation
        """
        query = select(UserAccount).options().where(UserAccount.id == user_id)
        result = await db.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            logger.warning(f"User {user_id} not found for anonymization")
            return {"message": "User not found"}

        # Generate anonymized data
        anonymous_uuid = str(uuid.uuid4())[:8]
        anonymized_email = f"deleted_{user_id}_{anonymous_uuid}@deleted.reelin.app"

        # Anonymize user account
        user.email = anonymized_email
        user.password_hash = None  # Clear password
        user.is_active = False
        user.avatar_url = None

        # Cancel Stripe subscription if exists
        if user.pro_stripe_subscription_id:
            try:
                from app.services.stripe_subscription import stripe_subscription_service
                await stripe_subscription_service.cancel_subscription(
                    user.pro_stripe_subscription_id,
                    cancel_immediately=True
                )
                logger.info(f"Cancelled Stripe subscription for user {user_id}")
            except Exception as e:
                logger.error(f"Failed to cancel Stripe subscription for user {user_id}: {e}")

        # Clear Pro status
        user.is_pro = False
        user.pro_expires_at = None
        user.pro_plan_type = None

        # Get and anonymize profile
        profile_query = select(UserProfile).where(UserProfile.user_id == user_id)
        profile_result = await db.execute(profile_query)
        profile = profile_result.scalar_one_or_none()

        if profile:
            profile.first_name = "Deleted"
            profile.last_name = "User"
            profile.phone = None
            profile.bio = None
            profile.profile_picture_url = None
            profile.facebook_url = None
            profile.instagram_url = None
            profile.tiktok_url = None
            profile.youtube_url = None
            profile.country_id = None
            profile.city_id = None
            profile.is_deleted = True
            profile.deleted_at = datetime.now(timezone.utc)

        # Revoke all Pro grants
        await db.execute(
            update(ProGrant)
            .where(ProGrant.user_id == user_id, ProGrant.is_active == True)
            .values(
                is_active=False,
                revoked_at=datetime.now(timezone.utc),
                revoke_reason="Account deleted"
            )
        )

        # Delete OAuth connections
        await db.execute(
            delete(SocialAccount).where(SocialAccount.user_id == user_id)
        )

        # Delete device tokens (should already be done, but just in case)
        await db.execute(
            delete(UserDeviceToken).where(UserDeviceToken.user_id == user_id)
        )

        # Log the action (using system user ID 0 or the user's own ID)
        audit_log = ProAuditLog(
            admin_id=user_id,  # System action logged under user's ID
            user_id=user_id,
            action="account_permanently_deleted",
            details={
                "anonymized_email": anonymized_email,
                "original_deletion_scheduled_at": user.deletion_scheduled_at.isoformat() if user.deletion_scheduled_at else None
            },
            reason="Grace period expired - automatic anonymization"
        )
        db.add(audit_log)

        # Clear the deletion scheduled timestamp
        user.deletion_scheduled_at = None

        await db.commit()

        logger.info(f"Account permanently anonymized for user {user_id}")

        return {
            "message": "Account permanently anonymized",
            "anonymized_at": datetime.now(timezone.utc),
            "anonymized_email": anonymized_email
        }

    async def get_pending_deletion_users(
        self,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20
    ) -> dict:
        """
        Get list of users with pending deletion (for admin).

        Args:
            db: Database session
            page: Page number
            page_size: Items per page

        Returns:
            Paginated list of users pending deletion
        """
        grace_period_days = await self.get_grace_period_days(db)

        # Count total
        count_query = select(UserAccount).where(
            UserAccount.deletion_scheduled_at.isnot(None)
        )
        count_result = await db.execute(count_query)
        total = len(count_result.scalars().all())

        # Get paginated results
        query = (
            select(UserAccount)
            .where(UserAccount.deletion_scheduled_at.isnot(None))
            .order_by(UserAccount.deletion_scheduled_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(query)
        users = result.scalars().all()

        now = datetime.now(timezone.utc)
        items = []
        for user in users:
            permanent_deletion_at = user.deletion_scheduled_at + timedelta(days=grace_period_days)
            days_remaining = max(0, (permanent_deletion_at - now).days)

            items.append({
                "id": user.id,
                "email": user.email,
                "deletion_scheduled_at": user.deletion_scheduled_at,
                "permanent_deletion_at": permanent_deletion_at,
                "days_remaining": days_remaining,
                "can_recover": days_remaining > 0
            })

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "grace_period_days": grace_period_days
        }

    async def get_deleted_users(
        self,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20
    ) -> dict:
        """
        Get list of permanently deleted (anonymized) users (for admin).

        Args:
            db: Database session
            page: Page number
            page_size: Items per page

        Returns:
            Paginated list of deleted users
        """
        # Count total
        count_query = select(UserProfile).where(UserProfile.is_deleted == True)
        count_result = await db.execute(count_query)
        total = len(count_result.scalars().all())

        # Get paginated results
        query = (
            select(UserProfile)
            .where(UserProfile.is_deleted == True)
            .order_by(UserProfile.deleted_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(query)
        profiles = result.scalars().all()

        items = []
        for profile in profiles:
            items.append({
                "id": profile.user_id,
                "display_name": f"{profile.first_name} {profile.last_name}",
                "deleted_at": profile.deleted_at,
                "can_recover": False  # Permanently deleted
            })

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size
        }

    async def process_expired_deletions(self, db: AsyncSession) -> int:
        """
        Process all accounts past their grace period.

        This should be called by a background job daily.

        Args:
            db: Database session

        Returns:
            Number of accounts anonymized
        """
        grace_period_days = await self.get_grace_period_days(db)
        cutoff = datetime.now(timezone.utc) - timedelta(days=grace_period_days)

        # Find accounts past grace period
        query = select(UserAccount).where(
            UserAccount.deletion_scheduled_at.isnot(None),
            UserAccount.deletion_scheduled_at < cutoff
        )
        result = await db.execute(query)
        users = result.scalars().all()

        count = 0
        for user in users:
            try:
                await self.permanently_anonymize(user.id, db)
                count += 1
            except Exception as e:
                logger.error(f"Failed to anonymize user {user.id}: {e}")

        logger.info(f"Processed {count} expired account deletions")
        return count


# Singleton instance
account_deletion_service = AccountDeletionService()
