"""Celery tasks for push notifications.

Handles sending push notifications via Firebase Cloud Messaging (FCM).

Uses synchronous database access to avoid event loop conflicts in Celery workers.
Implements fan-out pattern for scalable notification delivery to many users.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from sqlalchemy import select, and_, delete
from sqlalchemy.orm import Session, sessionmaker, selectinload

from app.celery_app import celery_app
from app.database import sync_engine
from app.models.notification import (
    UserDeviceToken,
    UserNotificationPreferences,
    CatchNotificationLevel,
    Notification,
)
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.catch import Catch
from app.models.user import UserAccount, UserProfile
from app.services.push_notifications import send_push_notification, send_silent_push

# Rate limit for like notifications (1 hour cooldown per catch)
LIKE_NOTIFICATION_COOLDOWN = timedelta(hours=1)

logger = logging.getLogger(__name__)

# Create sync session maker for Celery tasks (avoids event loop issues)
SyncSessionLocal = sessionmaker(bind=sync_engine, autocommit=False, autoflush=False)

# Batch size for fan-out pattern (number of users per batch task)
NOTIFICATION_BATCH_SIZE = 100


def _get_user_tokens(user_ids: List[int]) -> List[str]:
    """Get FCM tokens for given user IDs (sync version)."""
    with SyncSessionLocal() as db:
        result = db.execute(
            select(UserDeviceToken.token)
            .where(UserDeviceToken.user_id.in_(user_ids))
            .distinct()
        )
        return list(result.scalars().all())


def _get_event_participant_ids(
    event_id: int,
    exclude_user_id: Optional[int] = None
) -> List[int]:
    """Get user IDs of participants in an event (sync version)."""
    with SyncSessionLocal() as db:
        query = select(EventEnrollment.user_id).where(
            and_(
                EventEnrollment.event_id == event_id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
            )
        )
        if exclude_user_id:
            query = query.where(EventEnrollment.user_id != exclude_user_id)

        result = db.execute(query)
        return list(result.scalars().all())


def _filter_users_by_catch_preference(
    user_ids: List[int],
    catch_user_id: int,
    level: str = CatchNotificationLevel.ALL
) -> List[int]:
    """Filter users based on their catch notification preferences (sync version).

    Args:
        user_ids: List of user IDs to filter
        catch_user_id: The user who made the catch
        level: Filter level - 'all', 'mine', 'none'

    Returns:
        Filtered list of user IDs who should receive the notification
    """
    if not user_ids:
        return []

    with SyncSessionLocal() as db:
        # Get preferences for all users
        result = db.execute(
            select(UserNotificationPreferences)
            .where(UserNotificationPreferences.user_id.in_(user_ids))
        )
        preferences = {p.user_id: p for p in result.scalars().all()}

        filtered_users = []
        for user_id in user_ids:
            pref = preferences.get(user_id)

            # Default to 'all' if no preference set
            catch_level = pref.notify_event_catches if pref else CatchNotificationLevel.ALL

            if catch_level == CatchNotificationLevel.NONE:
                # User doesn't want any catch notifications
                continue
            elif catch_level == CatchNotificationLevel.MINE:
                # User only wants their own catches
                if user_id == catch_user_id:
                    filtered_users.append(user_id)
            else:
                # CatchNotificationLevel.ALL - user wants all catches
                filtered_users.append(user_id)

        return filtered_users


def _cleanup_invalid_tokens(failed_tokens: List[str]):
    """Remove invalid tokens from database (sync version)."""
    if not failed_tokens:
        return

    with SyncSessionLocal() as db:
        db.execute(
            delete(UserDeviceToken).where(UserDeviceToken.token.in_(failed_tokens))
        )
        db.commit()
        logger.info(f"Cleaned up {len(failed_tokens)} invalid FCM tokens")


def _send_silent_push_to_tokens(tokens: List[str], data: dict) -> dict:
    """Send silent/data-only push to multiple tokens.

    Silent push delivers data even when notifications are disabled.
    Used for critical app control signals like GPS start/stop.

    Args:
        tokens: List of FCM tokens
        data: Data payload to send

    Returns:
        Dict with success_count and failure_count
    """
    if not tokens:
        return {"success_count": 0, "failure_count": 0}

    success_count = 0
    failure_count = 0
    failed_tokens = []

    for token in tokens:
        if send_silent_push(token, data):
            success_count += 1
        else:
            failure_count += 1
            failed_tokens.append(token)

    logger.info(f"Silent push: sent {success_count}/{len(tokens)}")
    return {
        "success_count": success_count,
        "failure_count": failure_count,
        "failed_tokens": failed_tokens,
    }


# =============================================================================
# BATCH WORKER TASKS (Fan-out pattern)
# These tasks handle sending notifications to a batch of users.
# They are called by the main tasks when there are many users to notify.
# =============================================================================

@celery_app.task(bind=True, max_retries=3)
def _send_notification_batch(
    self,
    user_ids: List[int],
    title: str,
    body: str,
    data: Optional[dict] = None,
    click_action: Optional[str] = None,
):
    """Low-level batch task: send notification to a batch of users.

    This is called by fan-out tasks to handle a subset of users in parallel.
    """
    try:
        tokens = _get_user_tokens(user_ids)

        if not tokens:
            return {"success_count": 0, "failure_count": 0, "batch_size": len(user_ids)}

        result = send_push_notification(
            tokens=tokens,
            title=title,
            body=body,
            data=data,
            click_action=click_action,
        )

        if result.get("failed_tokens"):
            _cleanup_invalid_tokens(result["failed_tokens"])

        result["batch_size"] = len(user_ids)
        return result

    except Exception as e:
        logger.error(f"Error in notification batch: {e}")
        raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3)
def _send_event_notification_batch(
    self,
    user_ids: List[int],
    title: str,
    body: str,
    data: dict,
    click_action: str,
    send_silent: bool = False,
):
    """Low-level batch task for event notifications with optional silent push.

    Used for event start/stop notifications where silent push is needed for GPS control.
    """
    try:
        tokens = _get_user_tokens(user_ids)

        if not tokens:
            return {"success_count": 0, "failure_count": 0, "batch_size": len(user_ids)}

        # Send visible notification
        result = send_push_notification(
            tokens=tokens,
            title=title,
            body=body,
            data=data,
            click_action=click_action,
        )

        # Also send silent push if requested (for GPS control signals)
        if send_silent:
            silent_result = _send_silent_push_to_tokens(tokens, data)
            result["silent_success"] = silent_result["success_count"]
            result["silent_failure"] = silent_result["failure_count"]

        if result.get("failed_tokens"):
            _cleanup_invalid_tokens(result["failed_tokens"])

        result["batch_size"] = len(user_ids)
        logger.info(f"Batch notification: {result['success_count']}/{len(tokens)} sent")
        return result

    except Exception as e:
        logger.error(f"Error in event notification batch: {e}")
        raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))


def _fan_out_notifications(
    user_ids: List[int],
    title: str,
    body: str,
    data: Optional[dict] = None,
    click_action: Optional[str] = None,
    send_silent: bool = False,
) -> dict:
    """Fan out notifications to multiple batch tasks for parallel processing.

    Args:
        user_ids: List of all user IDs to notify
        title: Notification title
        body: Notification body
        data: Optional data payload
        click_action: Optional click action
        send_silent: Whether to also send silent push (for GPS control)

    Returns:
        Dict with total_users and batches_created
    """
    if not user_ids:
        return {"total_users": 0, "batches_created": 0}

    # Split into batches
    batches = [
        user_ids[i:i + NOTIFICATION_BATCH_SIZE]
        for i in range(0, len(user_ids), NOTIFICATION_BATCH_SIZE)
    ]

    # Dispatch batch tasks
    for batch in batches:
        if send_silent:
            _send_event_notification_batch.delay(
                user_ids=batch,
                title=title,
                body=body,
                data=data,
                click_action=click_action,
                send_silent=True,
            )
        else:
            _send_notification_batch.delay(
                user_ids=batch,
                title=title,
                body=body,
                data=data,
                click_action=click_action,
            )

    logger.info(
        f"Fan-out: dispatched {len(batches)} batch tasks for {len(user_ids)} users"
    )

    return {
        "total_users": len(user_ids),
        "batches_created": len(batches),
        "batch_size": NOTIFICATION_BATCH_SIZE,
    }


# =============================================================================
# MAIN NOTIFICATION TASKS
# These are the public API for sending notifications.
# They gather user IDs and fan out to batch tasks for scalable delivery.
# =============================================================================

@celery_app.task(bind=True, max_retries=3)
def send_notification_to_users(
    self,
    user_ids: List[int],
    title: str,
    body: str,
    data: Optional[dict] = None,
    click_action: Optional[str] = None,
):
    """Send push notification to specific users.

    Args:
        user_ids: List of user IDs to notify
        title: Notification title
        body: Notification body
        data: Optional data payload
        click_action: Optional click action
    """
    try:
        tokens = _get_user_tokens(user_ids)

        if not tokens:
            logger.info(f"No FCM tokens found for users {user_ids}")
            return {"success_count": 0, "failure_count": 0}

        result = send_push_notification(
            tokens=tokens,
            title=title,
            body=body,
            data=data,
            click_action=click_action,
        )

        # Cleanup invalid tokens
        if result.get("failed_tokens"):
            _cleanup_invalid_tokens(result["failed_tokens"])

        return result

    except Exception as e:
        logger.error(f"Error sending notification: {e}")
        raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3)
def send_catch_response_notification(
    self,
    catch_owner_id: int,
    fish_species: str,
    fish_length: float,
    status: str,  # "approved" or "rejected"
    event_name: str,
    event_id: int,
    rejection_reason: Optional[str] = None,
):
    """Send notification to catch owner when their catch is validated/rejected.

    Args:
        catch_owner_id: The user who submitted the catch
        fish_species: Species of fish
        fish_length: Length in cm
        status: "approved" or "rejected"
        event_name: Name of the event
        event_id: Event ID for deep linking
        rejection_reason: Optional reason if rejected
    """
    try:
        # Get tokens for the catch owner
        tokens = _get_user_tokens([catch_owner_id])

        if not tokens:
            logger.info(f"No FCM tokens found for catch owner {catch_owner_id}")
            return {"success_count": 0, "failure_count": 0}

        # Build notification content
        if status == "approved":
            title = "Catch Approved! ✅"
            body = f"Your {fish_species} ({fish_length}cm) in {event_name} has been validated"
        else:
            title = "Catch Rejected ❌"
            body = f"Your {fish_species} ({fish_length}cm) in {event_name} was rejected"
            if rejection_reason:
                body += f": {rejection_reason}"

        # Send notification
        result = send_push_notification(
            tokens=tokens,
            title=title,
            body=body,
            data={
                "type": "catch_response",
                "event_id": str(event_id),
                "status": status,
            },
            click_action=f"/events/{event_id}/my-catches",
        )

        # Cleanup invalid tokens
        if result.get("failed_tokens"):
            _cleanup_invalid_tokens(result["failed_tokens"])

        logger.info(
            f"Catch response notification for user {catch_owner_id}: "
            f"sent to {result['success_count']}/{len(tokens)} devices"
        )

        return result

    except Exception as e:
        logger.error(f"Error sending catch response notification: {e}")
        raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3)
def send_catch_notification(
    self,
    event_id: int,
    catch_user_id: int,
    catch_user_name: str,
    fish_species: str,
    fish_weight: Optional[float] = None,
    fish_length: Optional[float] = None,
    event_name: Optional[str] = None,
):
    """Send catch notification to event participants based on their preferences.

    Uses fan-out pattern for scalable delivery to many participants.

    This respects user notification preferences:
    - 'all': Receive notifications for all catches in the event
    - 'mine': Only receive notifications for own catches
    - 'none': No catch notifications

    Args:
        event_id: The event ID
        catch_user_id: The user who made the catch
        catch_user_name: Display name of the catch user
        fish_species: Species of fish caught
        fish_weight: Optional weight in grams
        fish_length: Optional length in cm
        event_name: Optional event name for the notification
    """
    try:
        # Get all participants in the event (except the catcher)
        participant_ids = _get_event_participant_ids(event_id, exclude_user_id=catch_user_id)

        if not participant_ids:
            logger.info(f"No other participants in event {event_id}")
            return {"total_users": 0, "batches_created": 0}

        # Filter based on notification preferences
        filtered_user_ids = _filter_users_by_catch_preference(
            user_ids=participant_ids,
            catch_user_id=catch_user_id,
        )

        if not filtered_user_ids:
            logger.info(f"No users to notify after filtering preferences")
            return {"total_users": 0, "batches_created": 0}

        # Build notification content
        title = f"New Catch in {event_name}" if event_name else "New Catch!"

        # Build body with available info
        body_parts = [f"{catch_user_name} caught a {fish_species}"]
        if fish_weight:
            body_parts.append(f"{fish_weight}g")
        if fish_length:
            body_parts.append(f"{fish_length}cm")

        body = " - ".join(body_parts) if len(body_parts) > 1 else body_parts[0]

        # Fan out to batch tasks for parallel delivery
        result = _fan_out_notifications(
            user_ids=filtered_user_ids,
            title=title,
            body=body,
            data={
                "type": "catch",
                "event_id": str(event_id),
                "catch_user_id": str(catch_user_id),
            },
            click_action=f"/events/{event_id}/leaderboard",
        )

        logger.info(
            f"Catch notification for event {event_id}: "
            f"dispatched {result['batches_created']} batches for {result['total_users']} users"
        )

        return result

    except Exception as e:
        logger.error(f"Error sending catch notification: {e}")
        raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3)
def send_event_notification(
    self,
    event_id: int,
    title: str,
    body: str,
    notification_type: str = "event_update",
    include_organizer: bool = True,
):
    """Send notification to all participants of an event.

    Uses fan-out pattern for scalable delivery to many participants.

    Args:
        event_id: The event ID
        title: Notification title
        body: Notification body
        notification_type: Type of notification (event_start, event_end, etc.)
        include_organizer: Whether to include the organizer
    """
    try:
        participant_ids = _get_event_participant_ids(event_id)

        if not participant_ids:
            logger.info(f"No participants in event {event_id}")
            return {"total_users": 0, "batches_created": 0}

        # Fan out to batch tasks for parallel delivery
        result = _fan_out_notifications(
            user_ids=participant_ids,
            title=title,
            body=body,
            data={
                "type": notification_type,
                "event_id": str(event_id),
            },
            click_action=f"/events/{event_id}",
        )

        logger.info(
            f"Event notification for {event_id}: "
            f"dispatched {result['batches_created']} batches for {result['total_users']} users"
        )

        return result

    except Exception as e:
        logger.error(f"Error sending event notification: {e}")
        raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3)
def send_new_event_notification(
    self,
    event_id: int,
    event_name: str,
    event_type_id: int,
    organizer_id: int,
    country_id: Optional[int] = None,
):
    """Send notification about a new event to users based on their discovery preferences.

    Uses fan-out pattern for scalable delivery to potentially many users.

    This respects user notification preferences:
    - notify_events_from_country: Only notify if event is in user's country
    - notify_event_types: Only notify for specific event types
    - notify_from_clubs: Only notify for events from specific organizers/clubs

    Args:
        event_id: The new event ID
        event_name: Event name
        event_type_id: Event type ID
        organizer_id: Organizer user ID
        country_id: Country ID of the event (if any)
    """
    try:
        # Get eligible users using sync session
        with SyncSessionLocal() as db:
            from app.models.user import UserProfile

            # Get all users with notification preferences
            result = db.execute(
                select(UserNotificationPreferences)
                .options(selectinload(UserNotificationPreferences.user))
            )
            all_prefs = result.scalars().all()

            eligible_user_ids = []

            for pref in all_prefs:
                # Skip the organizer themselves
                if pref.user_id == organizer_id:
                    continue

                # Check event type preference
                if pref.notify_event_types:
                    if event_type_id not in pref.notify_event_types:
                        continue

                # Check club/organizer preference
                if pref.notify_from_clubs:
                    # TODO: Check if organizer is in one of the followed clubs
                    # For now, we check if the organizer ID is in the list
                    if organizer_id not in pref.notify_from_clubs:
                        continue

                # Check country preference
                if pref.notify_events_from_country and country_id:
                    # Get user's country
                    profile_result = db.execute(
                        select(UserProfile.country_id)
                        .where(UserProfile.user_id == pref.user_id)
                    )
                    user_country = profile_result.scalar()
                    if user_country and user_country != country_id:
                        continue

                eligible_user_ids.append(pref.user_id)

        if not eligible_user_ids:
            logger.info("No eligible users for new event notification")
            return {"total_users": 0, "batches_created": 0}

        # Fan out to batch tasks for parallel delivery
        result = _fan_out_notifications(
            user_ids=eligible_user_ids,
            title="New Event Available!",
            body=f"Check out: {event_name}",
            data={
                "type": "new_event",
                "event_id": str(event_id),
            },
            click_action=f"/events/{event_id}",
        )

        logger.info(
            f"New event notification: "
            f"dispatched {result['batches_created']} batches for {result['total_users']} users"
        )

        return result

    except Exception as e:
        logger.error(f"Error sending new event notification: {e}")
        raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3)
def send_event_started_notifications(
    self,
    event_id: int,
    event_name: str,
):
    """Send FCM push notification to all enrolled users when event starts.

    Uses fan-out pattern for scalable delivery to many participants.
    Sends both visible notification and silent push to ensure delivery
    even when user has disabled notifications (for GPS auto-start).

    Args:
        event_id: The event ID
        event_name: Event name for the notification
    """
    try:
        participant_ids = _get_event_participant_ids(event_id)

        if not participant_ids:
            logger.info(f"No participants in event {event_id} for start notification")
            return {"total_users": 0, "batches_created": 0}

        # Data payload for both visible and silent push
        data_payload = {
            "type": "event_started",
            "event_id": str(event_id),
            "action": "start_gps",  # Signal for GPS auto-start
        }

        # Fan out to batch tasks for parallel delivery (with silent push enabled)
        result = _fan_out_notifications(
            user_ids=participant_ids,
            title="Event Started",
            body=f"{event_name} has started! Start submitting catches.",
            data=data_payload,
            click_action=f"/events/{event_id}",
            send_silent=True,  # Critical: send silent push for GPS control
        )

        logger.info(
            f"Event started notification for {event_id}: "
            f"dispatched {result['batches_created']} batches for {result['total_users']} users"
        )

        return result

    except Exception as e:
        logger.error(f"Error sending event started notification: {e}")
        raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3)
def send_event_stopped_notifications(
    self,
    event_id: int,
    event_name: str,
):
    """Send FCM push notification to all enrolled users when event stops.

    Uses fan-out pattern for scalable delivery to many participants.
    Sends both visible notification and silent push to ensure delivery
    even when user has disabled notifications (for GPS auto-stop).

    Args:
        event_id: The event ID
        event_name: Event name for the notification
    """
    try:
        participant_ids = _get_event_participant_ids(event_id)

        if not participant_ids:
            logger.info(f"No participants in event {event_id} for stop notification")
            return {"total_users": 0, "batches_created": 0}

        # Data payload for both visible and silent push
        data_payload = {
            "type": "event_stopped",
            "event_id": str(event_id),
            "action": "stop_gps",  # Signal for GPS auto-stop
        }

        # Fan out to batch tasks for parallel delivery (with silent push enabled)
        result = _fan_out_notifications(
            user_ids=participant_ids,
            title="Event Ended",
            body=f"{event_name} has ended. Thanks for participating!",
            data=data_payload,
            click_action=f"/events/{event_id}",
            send_silent=True,  # Critical: send silent push for GPS control
        )

        logger.info(
            f"Event stopped notification for {event_id}: "
            f"dispatched {result['batches_created']} batches for {result['total_users']} users"
        )

        return result

    except Exception as e:
        logger.error(f"Error sending event stopped notification: {e}")
        raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))


# =============================================================================
# CATCH LIKE NOTIFICATIONS
# =============================================================================


@celery_app.task(bind=True, max_retries=3)
def send_catch_like_notification(
    self,
    catch_id: int,
    liker_user_id: int,
):
    """Send notification to catch owner when someone likes their photo.

    Implements rate limiting: only one notification per catch per hour.

    Args:
        catch_id: The catch that was liked
        liker_user_id: The user who liked the catch
    """
    try:
        with SyncSessionLocal() as db:
            # Get catch with owner info
            catch = db.execute(
                select(Catch)
                .options(selectinload(Catch.user).selectinload(UserAccount.profile))
                .options(selectinload(Catch.fish))
                .where(Catch.id == catch_id)
            ).scalar_one_or_none()

            if not catch:
                logger.warning(f"Catch {catch_id} not found for like notification")
                return {"status": "catch_not_found"}

            # Check rate limit
            now = datetime.now(timezone.utc)
            if catch.last_like_notification_at:
                cooldown_expires = catch.last_like_notification_at + LIKE_NOTIFICATION_COOLDOWN
                if now < cooldown_expires:
                    logger.info(
                        f"Like notification for catch {catch_id} rate limited "
                        f"(cooldown expires at {cooldown_expires})"
                    )
                    return {"status": "rate_limited"}

            # Get liker's name
            liker = db.execute(
                select(UserAccount)
                .options(selectinload(UserAccount.profile))
                .where(UserAccount.id == liker_user_id)
            ).scalar_one_or_none()

            liker_name = "Someone"
            if liker and liker.profile:
                first = liker.profile.first_name or ""
                last = liker.profile.last_name or ""
                liker_name = f"{first} {last}".strip() or liker_name

            fish_name = catch.fish.name if catch.fish else "fish"

            # Build notification content
            title = "Someone liked your catch!"
            body = f"{liker_name} liked your {fish_name} catch"

            # Create in-app notification
            notification = Notification(
                user_id=catch.user_id,
                type="catch_like",
                title=title,
                message=body,
                data={
                    "catch_id": catch_id,
                    "event_id": catch.event_id,
                    "liker_id": liker_user_id,
                },
            )
            db.add(notification)

            # Update rate limit timestamp
            catch.last_like_notification_at = now
            db.commit()

        # Send push notification (outside transaction)
        tokens = _get_user_tokens([catch.user_id])

        if not tokens:
            logger.info(f"No FCM tokens for catch owner {catch.user_id}")
            return {"status": "no_tokens", "notification_created": True}

        result = send_push_notification(
            tokens=tokens,
            title=title,
            body=body,
            data={
                "type": "catch_like",
                "catch_id": str(catch_id),
                "event_id": str(catch.event_id),
            },
            click_action=f"/events/{catch.event_id}",
        )

        if result.get("failed_tokens"):
            _cleanup_invalid_tokens(result["failed_tokens"])

        logger.info(f"Like notification sent for catch {catch_id}: {result}")
        return {"status": "sent", **result}

    except Exception as e:
        logger.error(f"Error sending catch like notification: {e}")
        raise self.retry(exc=e, countdown=10 * (2 ** self.request.retries))
