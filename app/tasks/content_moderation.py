"""Celery tasks for profile picture content moderation.

Handles synchronous moderation of user profile pictures using AI services
(Google Vision Safe Search or ModerateContent.com fallback).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import sync_engine
from app.models.profile_moderation import (
    ProfilePictureModeration,
    ModerationStatus,
)
from app.models.user import UserAccount, UserProfile
from app.services.content_moderation_service import content_moderation_service

logger = logging.getLogger(__name__)


# Push notification messages for rejected profile pictures
REJECTION_NOTIFICATION = {
    "title": "Profile Picture Update",
    "body": "Your profile picture was not approved. Please upload a different image.",
}


def _moderate_profile_picture_sync(user_id: int, image_url: str) -> dict:
    """
    Run content moderation for a profile picture (synchronous).

    1. Create moderation record
    2. Call AI moderation service
    3. Update user profile status
    4. Send notification if rejected
    """
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=sync_engine)

    with SessionLocal() as db:
        # Get user and profile
        user = db.query(UserAccount).filter(UserAccount.id == user_id).first()

        if not user or not user.profile:
            logger.error(f"User {user_id} not found for moderation")
            return {"status": "error", "message": "User not found"}

        profile = user.profile

        # Create moderation record
        moderation = ProfilePictureModeration(
            user_id=user_id,
            image_url=image_url,
            status=ModerationStatus.PROCESSING.value,
        )
        db.add(moderation)
        db.commit()
        db.refresh(moderation)

        try:
            # Call AI moderation service (synchronous)
            result = content_moderation_service.moderate_image_sync(image_url)

            # Update moderation record
            moderation.status = (
                ModerationStatus.APPROVED.value
                if result.approved
                else ModerationStatus.REJECTED.value
            )
            moderation.adult_score = result.adult_score
            moderation.violence_score = result.violence_score
            moderation.racy_score = result.racy_score
            moderation.rejection_reason = result.rejection_reason
            moderation.processing_time_ms = result.processing_time_ms
            moderation.raw_response = result.raw_response
            moderation.processed_at = datetime.now(timezone.utc)
            # Enhanced detection fields
            moderation.detected_labels = result.detected_labels if result.detected_labels else None
            moderation.detected_text = result.detected_text
            moderation.offensive_labels_found = result.offensive_labels_found if result.offensive_labels_found else None
            moderation.offensive_text_found = result.offensive_text_found if result.offensive_text_found else None

            if result.approved:
                # Update profile status to approved
                profile.profile_picture_status = "approved"
                logger.info(
                    f"Profile picture approved for user {user_id} (provider: {result.provider})"
                )
            else:
                # Rejected - clear profile picture and notify user
                profile.profile_picture_status = "rejected"
                profile.profile_picture_url = None  # Clear the image

                logger.warning(
                    f"Profile picture rejected for user {user_id} "
                    f"(reason: {result.rejection_reason}, provider: {result.provider})"
                )

                # Send push notification
                _send_rejection_notification_sync(user_id)

            db.commit()

            return {
                "status": moderation.status,
                "user_id": user_id,
                "approved": result.approved,
                "rejection_reason": result.rejection_reason,
                "provider": result.provider,
            }

        except Exception as e:
            # Mark as failed
            moderation.status = ModerationStatus.FAILED.value
            moderation.error_message = str(e)
            moderation.processed_at = datetime.now(timezone.utc)

            # On failure, approve by default (fail-open for UX)
            profile.profile_picture_status = "approved"

            db.commit()

            logger.error(
                f"Content moderation failed for user {user_id}: {e}",
                exc_info=True,
            )
            return {
                "status": "failed",
                "user_id": user_id,
                "error": str(e),
            }


def _send_rejection_notification_sync(user_id: int) -> None:
    """Send push notification to user about rejected profile picture (synchronous)."""
    try:
        from sqlalchemy.orm import sessionmaker
        from app.models.notification import UserDeviceToken
        from app.services.push_notifications import send_push_notification_sync

        SessionLocal = sessionmaker(bind=sync_engine)

        with SessionLocal() as db:
            # Get user's device tokens
            tokens = db.query(UserDeviceToken.token).filter(
                UserDeviceToken.user_id == user_id
            ).all()
            tokens = [t[0] for t in tokens]

            if not tokens:
                logger.info(f"No device tokens for user {user_id}, skipping notification")
                return

            # Send to all user devices
            for token in tokens:
                try:
                    send_push_notification_sync(
                        token=token,
                        title=REJECTION_NOTIFICATION["title"],
                        body=REJECTION_NOTIFICATION["body"],
                        data={"type": "profile_picture_rejected"},
                    )
                except Exception as e:
                    logger.warning(f"Failed to send notification to token: {e}")

            logger.info(f"Sent rejection notification to user {user_id}")

    except ImportError:
        # send_push_notification_sync may not exist, try async version
        logger.warning(f"Sync push notification not available, skipping for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to send rejection notification to user {user_id}: {e}")


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def moderate_profile_picture_task(self, user_id: int, image_url: str) -> dict:
    """
    Background task to moderate a profile picture.

    Runs after profile picture is uploaded - does not block user experience.

    Args:
        user_id: The ID of the user whose picture to moderate
        image_url: URL of the image to check

    Returns:
        Moderation result dict
    """
    logger.info(f"Starting profile picture moderation for user {user_id}")

    try:
        result = _moderate_profile_picture_sync(user_id, image_url)
        return result

    except Exception as e:
        logger.error(f"Profile picture moderation task failed for user {user_id}: {e}")
        raise self.retry(exc=e)


def queue_profile_picture_moderation(user_id: int, image_url: str, delay_seconds: int = 2) -> None:
    """
    Queue a profile picture for content moderation.

    Called after profile picture URL is updated.

    Args:
        user_id: The ID of the user
        image_url: URL of the uploaded image
        delay_seconds: Delay before processing (allows image to finish uploading)
    """
    moderate_profile_picture_task.apply_async(
        args=[user_id, image_url],
        countdown=delay_seconds,
    )
    logger.info(f"Queued profile picture moderation for user {user_id}")
