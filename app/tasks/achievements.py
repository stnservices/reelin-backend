"""Celery tasks for achievements and statistics processing.

Handles:
- Processing achievements after catch validation
- Processing achievements after event completion
- Recalculating user statistics
- Sending achievement unlock notifications
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List

from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from app.celery_app import celery_app
from app.database import create_celery_session_maker
from app.services.achievement_service import achievement_service
from app.services.statistics_service import statistics_service
from app.models.event import Event, EventStatus
from app.models.catch import Catch, CatchStatus
from app.models.achievement import AchievementDefinition

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run async coroutine in Celery worker with fresh event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _process_catch_achievements(
    catch_id: int,
    event_id: int,
    user_id: int,
) -> List[int]:
    """Process achievements for a validated catch."""
    session_maker = create_celery_session_maker()
    async with session_maker() as db:
        try:
            # Get catch details with fish info
            from app.models.fish import Fish
            catch_stmt = (
                select(Catch)
                .where(Catch.id == catch_id)
                .options(selectinload(Catch.fish))
            )
            result = await db.execute(catch_stmt)
            catch = result.scalar_one_or_none()

            if not catch or catch.status != CatchStatus.APPROVED.value:
                return []

            # Get event details for time-based achievements
            event = await db.get(Event, event_id)
            if not event:
                return []

            # Build context for achievement checking
            context = {
                "catch_length": catch.length,
                "catch_weight": catch.weight,
                "fish_id": catch.fish_id,
                "fish_slug": catch.fish.slug if catch.fish else None,
            }

            # Check if personal best (largest catch by this user)
            from sqlalchemy import func
            largest_stmt = (
                select(func.max(Catch.length))
                .where(Catch.user_id == user_id)
                .where(Catch.status == CatchStatus.APPROVED.value)
                .where(Catch.id != catch_id)
            )
            result = await db.execute(largest_stmt)
            prev_best = result.scalar() or 0
            context["is_personal_best"] = catch.length > prev_best

            # Check early bird (first 30 minutes)
            early_cutoff = event.start_date + timedelta(minutes=30)
            catch_time = catch.catch_time or catch.submitted_at
            context["is_early_bird"] = catch_time <= early_cutoff

            # Check last minute (final 30 minutes)
            late_cutoff = event.end_date - timedelta(minutes=30)
            context["is_last_minute"] = catch_time >= late_cutoff

            # Update statistics first
            await statistics_service.update_user_stats_for_event(db, user_id, event_id)

            # Check and award achievements
            newly_awarded = await achievement_service.check_and_award_achievements(
                db,
                user_id,
                trigger="catch_approved",
                event_id=event_id,
                catch_id=catch_id,
                context=context,
            )

            await db.commit()

            # Return IDs of newly awarded achievements for notification
            return [a.id for a in newly_awarded]

        except Exception as e:
            logger.error(f"Error processing catch achievements: {e}")
            await db.rollback()
            return []


async def _process_event_completion_achievements(
    event_id: int,
    user_id: int,
    final_rank: int,
    initial_rank: Optional[int] = None,
) -> List[int]:
    """Process achievements when an event completes."""
    session_maker = create_celery_session_maker()
    async with session_maker() as db:
        try:
            # Build context
            context = {
                "final_rank": final_rank,
                "rank_improvement": (initial_rank - final_rank) if initial_rank else 0,
            }

            # Update statistics
            await statistics_service.update_user_stats_for_event(db, user_id, event_id)

            # Check and award achievements
            newly_awarded = await achievement_service.check_and_award_achievements(
                db,
                user_id,
                trigger="event_completed",
                event_id=event_id,
                context=context,
            )

            await db.commit()

            return [a.id for a in newly_awarded]

        except Exception as e:
            logger.error(f"Error processing event completion achievements: {e}")
            await db.rollback()
            return []


async def _recalculate_user_stats(user_id: int) -> bool:
    """Recalculate all statistics for a user."""
    session_maker = create_celery_session_maker()
    async with session_maker() as db:
        try:
            await statistics_service.recalculate_all_stats(db, user_id)
            await db.commit()
            return True
        except Exception as e:
            logger.error(f"Error recalculating user stats: {e}")
            await db.rollback()
            return False


async def _send_achievement_notification(
    user_id: int,
    achievement_id: int,
    event_id: Optional[int] = None,
) -> bool:
    """Send push notification and in-app (bell) notification for achievement unlock."""
    try:
        session_maker = create_celery_session_maker()
        async with session_maker() as db:
            # Get achievement details
            achievement = await db.get(AchievementDefinition, achievement_id)
            if not achievement:
                return False

            # Get event name if applicable
            event_name = None
            if event_id:
                event = await db.get(Event, event_id)
                event_name = event.name if event else None

            # Import here to avoid circular dependency
            from app.tasks.notifications import send_notification_to_users
            from app.models.notification import Notification

            title = "Achievement Unlocked!"
            message = f"Congratulations! You earned the '{achievement.name}' badge!"
            if event_name:
                message += f" (from {event_name})"

            notification_data = {
                "type": "achievement_unlocked",
                "achievement_id": achievement_id,
                "achievement_code": achievement.code,
                "achievement_name": achievement.name,
                "achievement_tier": achievement.tier,
                "achievement_category": achievement.category,
                "event_id": event_id,
            }

            # Create in-app (bell) notification
            in_app_notification = Notification(
                user_id=user_id,
                type="achievement_unlocked",
                title=title,
                message=message,
                data=notification_data,
            )
            db.add(in_app_notification)
            await db.commit()

            # Queue the push notification
            send_notification_to_users.delay(
                user_ids=[user_id],
                title=title,
                body=message,
                data=notification_data,
            )

            return True

    except Exception as e:
        logger.error(f"Error sending achievement notification: {e}")
        return False


# === Celery Tasks ===


@celery_app.task(name="achievements.process_catch")
def process_achievements_for_catch(catch_id: int, event_id: int, user_id: int):
    """
    Process achievements after a catch is validated.
    Called when a catch status changes to 'approved'.
    """
    try:
        awarded_ids = _run_async(
            _process_catch_achievements(catch_id, event_id, user_id)
        )

        # Send notifications for newly awarded achievements
        for achievement_id in awarded_ids:
            send_achievement_notification.delay(user_id, achievement_id, event_id)

        logger.info(f"Processed catch {catch_id} achievements for user {user_id}: {len(awarded_ids)} awarded")
        return awarded_ids

    except Exception as e:
        logger.error(f"Failed to process catch achievements: {e}")
        raise


@celery_app.task(name="achievements.process_event_completion")
def process_achievements_for_event_completion(
    event_id: int,
    user_id: int,
    final_rank: int,
    initial_rank: Optional[int] = None,
):
    """
    Process achievements when an event completes.
    Called for each participant when event status changes to 'completed'.
    """
    try:
        awarded_ids = _run_async(
            _process_event_completion_achievements(event_id, user_id, final_rank, initial_rank)
        )

        # Send notifications for newly awarded achievements
        for achievement_id in awarded_ids:
            send_achievement_notification.delay(user_id, achievement_id, event_id)

        logger.info(f"Processed event {event_id} completion for user {user_id}: {len(awarded_ids)} awarded")
        return awarded_ids

    except Exception as e:
        logger.error(f"Failed to process event completion achievements: {e}")
        raise


@celery_app.task(name="achievements.recalculate_stats")
def recalculate_user_statistics(user_id: int):
    """
    Recalculate all statistics for a user.
    Use for data corrections or after migrations.
    """
    try:
        success = _run_async(_recalculate_user_stats(user_id))
        logger.info(f"Recalculated stats for user {user_id}: {'success' if success else 'failed'}")
        return success

    except Exception as e:
        logger.error(f"Failed to recalculate user stats: {e}")
        raise


@celery_app.task(name="achievements.send_notification")
def send_achievement_notification(
    user_id: int,
    achievement_id: int,
    event_id: Optional[int] = None,
):
    """Send push notification for achievement unlock."""
    try:
        success = _run_async(
            _send_achievement_notification(user_id, achievement_id, event_id)
        )
        return success

    except Exception as e:
        logger.error(f"Failed to send achievement notification: {e}")
        raise


@celery_app.task(name="achievements.batch_recalculate_all_users")
def batch_recalculate_all_users_statistics():
    """
    Batch job to recalculate statistics for all users.
    Run as maintenance task (e.g., weekly).
    """
    from app.models.user import UserAccount

    async def _batch_recalculate():
        session_maker = create_celery_session_maker()
        async with session_maker() as db:
            result = await db.execute(
                select(UserAccount.id).where(UserAccount.is_active == True)
            )
            user_ids = result.scalars().all()

        # Queue individual recalculations
        for user_id in user_ids:
            recalculate_user_statistics.delay(user_id)

        return len(user_ids)

    try:
        count = _run_async(_batch_recalculate())
        logger.info(f"Queued stats recalculation for {count} users")
        return count

    except Exception as e:
        logger.error(f"Failed to batch recalculate stats: {e}")
        raise


@celery_app.task(name="achievements.recalculate_all_achievements")
def recalculate_all_achievements(send_notifications: bool = False):
    """
    Recalculate achievements for ALL users who participated in completed events.
    This is a long-running task that should be run after achievement logic changes.
    """
    from app.models.user import UserAccount
    from app.models.event import EventEnrollment
    from sqlalchemy import func, distinct

    async def _recalculate_all():
        session_maker = create_celery_session_maker()
        async with session_maker() as db:
            # Get all users who have participated in at least one completed event
            result = await db.execute(
                select(distinct(EventEnrollment.user_id))
                .join(Event, Event.id == EventEnrollment.event_id)
                .where(EventEnrollment.status == "approved")
                .where(Event.status == "completed")
                .where(Event.is_test == False)
            )
            user_ids = result.scalars().all()
            return user_ids

    try:
        user_ids = _run_async(_recalculate_all())
        logger.info(f"Starting achievement recalculation for {len(user_ids)} users")

        # Queue individual recalculations with delay to avoid overwhelming the system
        for i, user_id in enumerate(user_ids):
            recalculate_user_achievements.apply_async(
                args=[user_id, send_notifications],
                countdown=i * 2,  # 2 second delay between each user
            )

        return {"queued": len(user_ids), "user_ids": user_ids}

    except Exception as e:
        logger.error(f"Failed to start bulk achievement recalculation: {e}")
        raise


@celery_app.task(name="achievements.recalculate_user")
def recalculate_user_achievements(user_id: int, send_notifications: bool = False):
    """
    Recalculate all achievements for a single user.
    Checks all their catches and completed events for missed achievements.
    """
    from app.models.user import UserAccount
    from app.models.event import EventEnrollment
    from app.models.fish import Fish
    from sqlalchemy import func

    async def _recalculate_user():
        session_maker = create_celery_session_maker()
        async with session_maker() as db:
            # Verify user exists
            user = await db.get(UserAccount, user_id)
            if not user:
                logger.warning(f"User {user_id} not found for achievement recalculation")
                return {"error": "User not found"}

            new_achievements = []

            # Get user's completed events
            events_query = (
                select(Event.id, Event.event_type_id)
                .join(EventEnrollment, EventEnrollment.event_id == Event.id)
                .where(EventEnrollment.user_id == user_id)
                .where(EventEnrollment.status == "approved")
                .where(Event.status == "completed")
                .where(Event.is_test == False)
                .order_by(Event.end_date)
            )
            events_result = await db.execute(events_query)
            events = events_result.fetchall()

            # Get user's approved catches with fish info
            catches_query = (
                select(Catch)
                .join(Event, Event.id == Catch.event_id)
                .options(selectinload(Catch.fish))
                .where(Catch.user_id == user_id)
                .where(Catch.status == CatchStatus.APPROVED.value)
                .where(Event.is_test == False)
                .order_by(Catch.submitted_at)
            )
            catches_result = await db.execute(catches_query)
            catches = catches_result.scalars().all()

            max_length_seen = 0.0

            # Process each catch
            for catch in catches:
                event = await db.get(Event, catch.event_id)
                if not event or not event.event_type:
                    continue

                format_code = "sf" if event.event_type.code == "street_fishing" else "ta"

                catch_time = catch.catch_time or catch.submitted_at
                early_cutoff = event.start_date + timedelta(minutes=30)
                late_cutoff = event.end_date - timedelta(minutes=30)

                is_early_bird = catch_time <= early_cutoff if catch_time and event.start_date else False
                is_last_minute = catch_time >= late_cutoff if catch_time and event.end_date else False
                is_personal_best = catch.length > max_length_seen if catch.length else False

                if catch.length and catch.length > max_length_seen:
                    max_length_seen = catch.length

                context = {
                    "catch_length": catch.length,
                    "catch_weight": catch.weight,
                    "fish_id": catch.fish_id,
                    "fish_slug": catch.fish.slug if catch.fish else None,
                    "is_early_bird": is_early_bird,
                    "is_last_minute": is_last_minute,
                    "is_personal_best": is_personal_best,
                }

                awarded = await achievement_service.check_and_award_achievements(
                    db,
                    user_id=user_id,
                    trigger="catch_approved",
                    event_id=catch.event_id,
                    catch_id=catch.id,
                    context=context,
                    format_code=format_code,
                )

                for ach in awarded:
                    if ach.code not in new_achievements:
                        new_achievements.append(ach.code)
                        if send_notifications:
                            send_achievement_notification.delay(user_id, ach.id, catch.event_id)

            # Process each completed event
            for event_row in events:
                event = await db.get(Event, event_row.id)
                if not event or not event.event_type:
                    continue

                format_code = "sf" if event.event_type.code == "street_fishing" else "ta"

                awarded = await achievement_service.check_and_award_achievements(
                    db,
                    user_id=user_id,
                    trigger="event_completed",
                    event_id=event_row.id,
                    format_code=format_code,
                )

                for ach in awarded:
                    if ach.code not in new_achievements:
                        new_achievements.append(ach.code)
                        if send_notifications:
                            send_achievement_notification.delay(user_id, ach.id, event_row.id)

            await db.commit()
            return {"user_id": user_id, "new_achievements": new_achievements}

    try:
        result = _run_async(_recalculate_user())
        logger.info(f"Recalculated achievements for user {user_id}: {result.get('new_achievements', [])}")
        return result

    except Exception as e:
        logger.error(f"Failed to recalculate achievements for user {user_id}: {e}")
        raise
