"""Account Deletion service for GDPR compliance and app store requirements.

This module handles account deletion with a configurable grace period,
allowing users to recover their accounts before permanent anonymization.

Two-stage deletion process:
1. Schedule deletion (soft delete) - user can recover within grace period
2. Permanent anonymization - after grace period expires, data is scrambled

GDPR Article 17 Compliance:
- Erasure within 30 days (grace period allows recovery)
- Anonymization is accepted alternative to hard deletion
- Historical data (catches, enrollments) kept with anonymized reference
- User-owned content (waypoints, follows) is deleted
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
from app.models.notification import UserDeviceToken, Notification, UserNotificationPreferences
from app.models.event import Event, EventStatus
from app.models.waypoint import UserWaypoint
from app.models.follow import UserFollow
from app.models.recommendation import RecommendationDismissal
from app.models.club import ClubMembership, Club
from app.models.organizer_message import OrganizerMessage
from app.models.admin_message import AdminMessage
from app.models.minigame import MinigameScore
from app.models.achievement import UserAchievement, UserAchievementProgress, UserStreakTracker
from app.models.statistics import UserEventTypeStats
from app.models.contestation import EventContestation
from app.models.billing import OrganizerBillingProfile

logger = logging.getLogger(__name__)

# Anonymized display name for deleted users
FALLEN_ANGLER_NAME = "Fallen Angler"

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

        # Check if user is club owner with members
        club_query = select(Club).where(Club.owner_id == user_id)
        club_result = await db.execute(club_query)
        owned_clubs = club_result.scalars().all()

        for club in owned_clubs:
            # Check if club has other members
            members_query = select(ClubMembership).where(
                ClubMembership.club_id == club.id,
                ClubMembership.user_id != user_id,
                ClubMembership.status == "approved"
            )
            members_result = await db.execute(members_query)
            if members_result.scalars().first():
                constraints.append(f"You are the owner of club '{club.name}' with active members. Transfer ownership first.")

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
        Stage 2: Permanently anonymize user data (GDPR Article 17 compliant).

        This is irreversible and should only be called by the background job
        after the grace period has expired.

        Data handling:
        - PII (email, name, phone, etc.): Anonymized
        - User-owned content (waypoints, follows): Deleted
        - Historical data (catches, enrollments): Kept with "Fallen Angler" reference
        - Subscriptions: Cancelled
        - Media files: Avatar deleted, catch photos kept for event records

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

        deletion_stats = {
            "waypoints_deleted": 0,
            "follows_deleted": 0,
            "notifications_deleted": 0,
            "messages_anonymized": 0,
        }

        # Generate anonymized data
        anonymous_uuid = str(uuid.uuid4())[:8]
        anonymized_email = f"deleted_{user_id}_{anonymous_uuid}@deleted.reelin.app"

        # ============================================================
        # 0. SEND NOTIFICATION EMAIL (before PII is wiped)
        # ============================================================
        try:
            from app.services.email import get_email_service
            email_service = get_email_service()
            # Get first name from profile
            profile_q = await db.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
            user_profile = profile_q.scalar_one_or_none()
            first_name = user_profile.first_name if user_profile else "there"
            email_service.send_account_deleted_email(
                to_email=user.email,
                first_name=first_name,
            )
        except Exception as e:
            logger.warning(f"Failed to send account deleted email to user {user_id}: {e}")

        # ============================================================
        # 1. ANONYMIZE USER ACCOUNT
        # ============================================================
        old_avatar_url = user.avatar_url
        user.email = anonymized_email
        user.password_hash = None  # Clear password
        user.is_active = False
        user.avatar_url = None

        # Cancel Stripe subscription if exists (PRO user check)
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
        user.pro_stripe_customer_id = None
        user.pro_stripe_subscription_id = None
        user.pro_started_at = None

        # ============================================================
        # 2. ANONYMIZE USER PROFILE - "Fallen Angler" 🎣
        # ============================================================
        profile_query = select(UserProfile).where(UserProfile.user_id == user_id)
        profile_result = await db.execute(profile_query)
        profile = profile_result.scalar_one_or_none()

        old_profile_picture_url = None
        if profile:
            old_profile_picture_url = profile.profile_picture_url
            profile.first_name = f"{FALLEN_ANGLER_NAME} #{user_id}"
            profile.last_name = ""
            profile.phone = None
            profile.bio = None
            profile.profile_picture_url = None
            profile.facebook_url = None
            profile.instagram_url = None
            profile.tiktok_url = None
            profile.youtube_url = None
            profile.country_id = None
            profile.city_id = None
            profile.gender = None
            profile.is_profile_public = False
            profile.is_deleted = True
            profile.deleted_at = datetime.now(timezone.utc)

        # ============================================================
        # 3. DELETE USER-OWNED CONTENT
        # ============================================================

        # Delete waypoints (user's private fishing spots)
        waypoint_result = await db.execute(
            delete(UserWaypoint).where(UserWaypoint.user_id == user_id)
        )
        deletion_stats["waypoints_deleted"] = waypoint_result.rowcount

        # Delete follows (both directions - who user follows and who follows user)
        follows_deleted = 0
        result1 = await db.execute(
            delete(UserFollow).where(UserFollow.follower_id == user_id)
        )
        follows_deleted += result1.rowcount
        result2 = await db.execute(
            delete(UserFollow).where(UserFollow.following_id == user_id)
        )
        follows_deleted += result2.rowcount
        deletion_stats["follows_deleted"] = follows_deleted

        # Delete notifications
        notif_result = await db.execute(
            delete(Notification).where(Notification.user_id == user_id)
        )
        deletion_stats["notifications_deleted"] = notif_result.rowcount

        # Delete notification preferences
        await db.execute(
            delete(UserNotificationPreferences).where(UserNotificationPreferences.user_id == user_id)
        )

        # Delete recommendation dismissals
        await db.execute(
            delete(RecommendationDismissal).where(RecommendationDismissal.user_id == user_id)
        )

        # Delete club memberships (but not owned clubs - those are handled by constraints)
        await db.execute(
            delete(ClubMembership).where(ClubMembership.user_id == user_id)
        )

        # Delete minigame scores
        await db.execute(
            delete(MinigameScore).where(MinigameScore.user_id == user_id)
        )

        # Delete achievements and progress
        await db.execute(
            delete(UserAchievement).where(UserAchievement.user_id == user_id)
        )
        await db.execute(
            delete(UserAchievementProgress).where(UserAchievementProgress.user_id == user_id)
        )
        await db.execute(
            delete(UserStreakTracker).where(UserStreakTracker.user_id == user_id)
        )

        # Delete user statistics
        await db.execute(
            delete(UserEventTypeStats).where(UserEventTypeStats.user_id == user_id)
        )

        # ============================================================
        # 4. ANONYMIZE MESSAGES (keep for support history)
        # ============================================================

        # Anonymize organizer messages (clear sender snapshot PII)
        org_msg_result = await db.execute(
            update(OrganizerMessage)
            .where(OrganizerMessage.sender_id == user_id)
            .values(
                sender_name=f"{FALLEN_ANGLER_NAME} #{user_id}",
                sender_email=anonymized_email,
                sender_phone=None
            )
        )
        deletion_stats["messages_anonymized"] += org_msg_result.rowcount

        # Anonymize admin messages (clear sender snapshot PII)
        admin_msg_result = await db.execute(
            update(AdminMessage)
            .where(AdminMessage.sender_id == user_id)
            .values(
                sender_name=f"{FALLEN_ANGLER_NAME} #{user_id}",
                sender_email=anonymized_email,
                sender_phone=None
            )
        )
        deletion_stats["messages_anonymized"] += admin_msg_result.rowcount

        # ============================================================
        # 5. ANONYMIZE CONTESTATIONS (keep for audit trail)
        # ============================================================
        await db.execute(
            update(EventContestation)
            .where(EventContestation.reporter_user_id == user_id)
            .values(reporter_user_id=user_id)  # Keep ID but profile shows "Fallen Angler"
        )

        # ============================================================
        # 6. ANONYMIZE BILLING PROFILES (keep for tax/legal requirements)
        # ============================================================
        await db.execute(
            update(OrganizerBillingProfile)
            .where(OrganizerBillingProfile.user_id == user_id)
            .values(
                legal_name=f"{FALLEN_ANGLER_NAME} (Deleted)",
                billing_address_line1="[deleted]",
                billing_address_line2=None,
                billing_city="[deleted]",
                billing_county=None,
                billing_postal_code="00000",
                billing_email=anonymized_email,
                billing_phone=None,
                # Keep tax_id/CNP for legal compliance but mark as inactive
                is_active=False
            )
        )

        # ============================================================
        # 7. REVOKE SUBSCRIPTIONS AND GRANTS
        # ============================================================

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

        # Mark Pro subscriptions as cancelled
        await db.execute(
            update(ProSubscription)
            .where(ProSubscription.user_id == user_id)
            .values(
                status="cancelled",
                ended_at=datetime.now(timezone.utc)
            )
        )

        # ============================================================
        # 8. DELETE AUTH-RELATED DATA
        # ============================================================

        # Delete OAuth connections
        await db.execute(
            delete(SocialAccount).where(SocialAccount.user_id == user_id)
        )

        # Delete device tokens (should already be done, but ensure cleanup)
        await db.execute(
            delete(UserDeviceToken).where(UserDeviceToken.user_id == user_id)
        )

        # ============================================================
        # 9. DELETE MEDIA FILES FROM STORAGE
        # ============================================================
        try:
            from app.core.storage import storage_service

            # Delete avatar from storage
            if old_avatar_url:
                await storage_service.delete_file(old_avatar_url)
                logger.info(f"Deleted avatar for user {user_id}")

            # Delete profile picture from storage
            if old_profile_picture_url and old_profile_picture_url != old_avatar_url:
                await storage_service.delete_file(old_profile_picture_url)
                logger.info(f"Deleted profile picture for user {user_id}")

        except Exception as e:
            # Don't fail anonymization if media deletion fails
            logger.warning(f"Failed to delete media files for user {user_id}: {e}")

        # ============================================================
        # 10. AUDIT LOG
        # ============================================================
        audit_log = ProAuditLog(
            admin_id=user_id,  # System action logged under user's ID
            user_id=user_id,
            action="account_permanently_deleted",
            details={
                "anonymized_email": anonymized_email,
                "display_name": f"{FALLEN_ANGLER_NAME} #{user_id}",
                "original_deletion_scheduled_at": user.deletion_scheduled_at.isoformat() if user.deletion_scheduled_at else None,
                "deletion_stats": deletion_stats
            },
            reason="Grace period expired - automatic GDPR anonymization"
        )
        db.add(audit_log)

        # Clear the deletion scheduled timestamp
        user.deletion_scheduled_at = None

        await db.commit()

        logger.info(
            f"Account permanently anonymized for user {user_id}: "
            f"{deletion_stats['waypoints_deleted']} waypoints, "
            f"{deletion_stats['follows_deleted']} follows, "
            f"{deletion_stats['notifications_deleted']} notifications deleted"
        )

        return {
            "message": "Account permanently anonymized",
            "anonymized_at": datetime.now(timezone.utc),
            "anonymized_email": anonymized_email,
            "display_name": f"{FALLEN_ANGLER_NAME} #{user_id}",
            "deletion_stats": deletion_stats
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
