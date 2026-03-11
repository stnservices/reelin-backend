"""Celery tasks for achievements and statistics processing.

Handles:
- Processing achievements after catch validation
- Processing achievements after event completion
- Recalculating user statistics
- Sending achievement unlock notifications
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List

from sqlalchemy import select, func, distinct, and_
from sqlalchemy.orm import selectinload

from app.celery_app import celery_app
from app.database import SyncSessionLocal
from app.services.achievement_service import AchievementService, get_cached_achievement_sync
from app.services.statistics_service import StatisticsService
from app.models.event import Event, EventStatus
from app.models.catch import Catch, CatchStatus
from app.models.achievement import AchievementDefinition, UserAchievement

logger = logging.getLogger(__name__)


def _sync_process_catch_achievements(
    catch_id: int,
    event_id: int,
    user_id: int,
) -> List[int]:
    """Process achievements for a validated catch."""
    with SyncSessionLocal() as db:
        try:
            from app.models.fish import Fish
            catch_stmt = (
                select(Catch)
                .where(Catch.id == catch_id)
                .options(selectinload(Catch.fish))
            )
            result = db.execute(catch_stmt)
            catch = result.scalar_one_or_none()

            if not catch or catch.status != CatchStatus.APPROVED.value:
                return []

            event = db.get(Event, event_id)
            if not event:
                return []

            context = {
                "catch_length": catch.length,
                "catch_weight": catch.weight,
                "fish_id": catch.fish_id,
                "fish_slug": catch.fish.slug if catch.fish else None,
            }

            # Check if personal best
            largest_stmt = (
                select(func.max(Catch.length))
                .where(Catch.user_id == user_id)
                .where(Catch.status == CatchStatus.APPROVED.value)
                .where(Catch.id != catch_id)
            )
            result = db.execute(largest_stmt)
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
            StatisticsService.update_user_stats_for_event_sync(db, user_id, event_id)

            # Check and award achievements
            newly_awarded = AchievementService.check_and_award_achievements_sync(
                db,
                user_id,
                trigger="catch_approved",
                event_id=event_id,
                catch_id=catch_id,
                context=context,
            )

            db.commit()

            return [a.id for a in newly_awarded]

        except Exception as e:
            logger.error(f"Error processing catch achievements: {e}")
            db.rollback()
            return []


def _sync_process_event_completion_achievements(
    event_id: int,
    user_id: int,
    final_rank: int,
    initial_rank: Optional[int] = None,
) -> List[int]:
    """Process achievements when an event completes."""
    with SyncSessionLocal() as db:
        try:
            context = {
                "final_rank": final_rank,
                "rank_improvement": (initial_rank - final_rank) if initial_rank else 0,
            }

            StatisticsService.update_user_stats_for_event_sync(db, user_id, event_id)

            newly_awarded = AchievementService.check_and_award_achievements_sync(
                db,
                user_id,
                trigger="event_completed",
                event_id=event_id,
                context=context,
            )

            db.commit()

            return [a.id for a in newly_awarded]

        except Exception as e:
            logger.error(f"Error processing event completion achievements: {e}")
            db.rollback()
            return []


def _sync_recalculate_user_stats(user_id: int) -> bool:
    """Recalculate all statistics for a user."""
    with SyncSessionLocal() as db:
        try:
            StatisticsService.recalculate_all_stats_sync(db, user_id)
            db.commit()
            return True
        except Exception as e:
            logger.error(f"Error recalculating user stats: {e}")
            db.rollback()
            return False


def _sync_send_achievement_notification(
    user_id: int,
    achievement_id: int,
    event_id: Optional[int] = None,
) -> bool:
    """Send push notification and in-app notification for achievement unlock."""
    try:
        with SyncSessionLocal() as db:
            achievement = db.get(AchievementDefinition, achievement_id)
            if not achievement:
                return False

            event_name = None
            if event_id:
                event = db.get(Event, event_id)
                event_name = event.name if event else None

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

            in_app_notification = Notification(
                user_id=user_id,
                type="achievement_unlocked",
                title=title,
                message=message,
                data=notification_data,
            )
            db.add(in_app_notification)
            db.commit()

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


def _sync_check_hall_of_fame_achievements(
    db,
    user_id: int,
) -> List:
    """Check and award Hall of Fame achievements (SF/TA Champion)."""
    from app.models.hall_of_fame import HallOfFameEntry

    newly_awarded = []

    # Check for SF Champion
    sf_hof_stmt = (
        select(HallOfFameEntry.id)
        .where(HallOfFameEntry.user_id == user_id)
        .where(HallOfFameEntry.format_code == "sf")
        .where(HallOfFameEntry.position == 1)
        .where(HallOfFameEntry.achievement_type == "national_champion")
    )
    result = db.execute(sf_hof_stmt)
    has_sf_title = result.scalar() is not None

    if has_sf_title:
        existing_stmt = (
            select(UserAchievement.id)
            .join(AchievementDefinition, AchievementDefinition.id == UserAchievement.achievement_id)
            .where(UserAchievement.user_id == user_id)
            .where(AchievementDefinition.code == "sf_champion")
        )
        result = db.execute(existing_stmt)
        if not result.scalar():
            ach_stmt = select(AchievementDefinition).where(AchievementDefinition.code == "sf_champion")
            result = db.execute(ach_stmt)
            sf_ach = result.scalar_one_or_none()
            if sf_ach:
                user_ach = UserAchievement(
                    user_id=user_id,
                    achievement_id=sf_ach.id,
                )
                db.add(user_ach)
                newly_awarded.append(sf_ach)
                logger.info(f"Awarded SF Champion to user {user_id}")

    # Check for TA Champion
    ta_hof_stmt = (
        select(HallOfFameEntry.id)
        .where(HallOfFameEntry.user_id == user_id)
        .where(HallOfFameEntry.format_code == "ta")
        .where(HallOfFameEntry.position == 1)
        .where(HallOfFameEntry.achievement_type == "national_champion")
    )
    result = db.execute(ta_hof_stmt)
    has_ta_title = result.scalar() is not None

    if has_ta_title:
        existing_stmt = (
            select(UserAchievement.id)
            .join(AchievementDefinition, AchievementDefinition.id == UserAchievement.achievement_id)
            .where(UserAchievement.user_id == user_id)
            .where(AchievementDefinition.code == "ta_champion")
        )
        result = db.execute(existing_stmt)
        if not result.scalar():
            ach_stmt = select(AchievementDefinition).where(AchievementDefinition.code == "ta_champion")
            result = db.execute(ach_stmt)
            ta_ach = result.scalar_one_or_none()
            if ta_ach:
                user_ach = UserAchievement(
                    user_id=user_id,
                    achievement_id=ta_ach.id,
                )
                db.add(user_ach)
                newly_awarded.append(ta_ach)
                logger.info(f"Awarded TA Champion to user {user_id}")

    return newly_awarded


async def _check_hall_of_fame_achievements(
    db,
    user_id: int,
) -> List:
    """Async version of Hall of Fame achievement check for use in async endpoints."""
    from app.models.hall_of_fame import HallOfFameEntry

    newly_awarded = []

    # Check for SF Champion
    sf_hof_stmt = (
        select(HallOfFameEntry.id)
        .where(HallOfFameEntry.user_id == user_id)
        .where(HallOfFameEntry.format_code == "sf")
        .where(HallOfFameEntry.position == 1)
        .where(HallOfFameEntry.achievement_type == "national_champion")
    )
    result = await db.execute(sf_hof_stmt)
    has_sf_title = result.scalar() is not None

    if has_sf_title:
        existing_stmt = (
            select(UserAchievement.id)
            .join(AchievementDefinition, AchievementDefinition.id == UserAchievement.achievement_id)
            .where(UserAchievement.user_id == user_id)
            .where(AchievementDefinition.code == "sf_champion")
        )
        result = await db.execute(existing_stmt)
        if not result.scalar():
            ach_stmt = select(AchievementDefinition).where(AchievementDefinition.code == "sf_champion")
            result = await db.execute(ach_stmt)
            sf_ach = result.scalar_one_or_none()
            if sf_ach:
                user_ach = UserAchievement(
                    user_id=user_id,
                    achievement_id=sf_ach.id,
                )
                db.add(user_ach)
                newly_awarded.append(sf_ach)
                logger.info(f"Awarded SF Champion to user {user_id}")

    # Check for TA Champion
    ta_hof_stmt = (
        select(HallOfFameEntry.id)
        .where(HallOfFameEntry.user_id == user_id)
        .where(HallOfFameEntry.format_code == "ta")
        .where(HallOfFameEntry.position == 1)
        .where(HallOfFameEntry.achievement_type == "national_champion")
    )
    result = await db.execute(ta_hof_stmt)
    has_ta_title = result.scalar() is not None

    if has_ta_title:
        existing_stmt = (
            select(UserAchievement.id)
            .join(AchievementDefinition, AchievementDefinition.id == UserAchievement.achievement_id)
            .where(UserAchievement.user_id == user_id)
            .where(AchievementDefinition.code == "ta_champion")
        )
        result = await db.execute(existing_stmt)
        if not result.scalar():
            ach_stmt = select(AchievementDefinition).where(AchievementDefinition.code == "ta_champion")
            result = await db.execute(ach_stmt)
            ta_ach = result.scalar_one_or_none()
            if ta_ach:
                user_ach = UserAchievement(
                    user_id=user_id,
                    achievement_id=ta_ach.id,
                )
                db.add(user_ach)
                newly_awarded.append(ta_ach)
                logger.info(f"Awarded TA Champion to user {user_id}")

    return newly_awarded


# === Celery Tasks ===


@celery_app.task(name="achievements.process_catch")
def process_achievements_for_catch(catch_id: int, event_id: int, user_id: int):
    """
    Process achievements after a catch is validated.
    Called when a catch status changes to 'approved'.
    """
    try:
        awarded_ids = _sync_process_catch_achievements(catch_id, event_id, user_id)

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
        awarded_ids = _sync_process_event_completion_achievements(
            event_id, user_id, final_rank, initial_rank
        )

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
        success = _sync_recalculate_user_stats(user_id)
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
        success = _sync_send_achievement_notification(user_id, achievement_id, event_id)
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

    try:
        with SyncSessionLocal() as db:
            result = db.execute(
                select(UserAccount.id).where(UserAccount.is_active == True)
            )
            user_ids = result.scalars().all()

        for user_id in user_ids:
            recalculate_user_statistics.delay(user_id)

        logger.info(f"Queued stats recalculation for {len(user_ids)} users")
        return len(user_ids)

    except Exception as e:
        logger.error(f"Failed to batch recalculate stats: {e}")
        raise


@celery_app.task(name="achievements.recalculate_all_achievements")
def recalculate_all_achievements(send_notifications: bool = False):
    """
    Recalculate achievements for ALL users who participated in completed events.
    This is a long-running task that should be run after achievement logic changes.
    """
    from app.models.event import EventEnrollment

    try:
        with SyncSessionLocal() as db:
            result = db.execute(
                select(distinct(EventEnrollment.user_id))
                .join(Event, Event.id == EventEnrollment.event_id)
                .where(EventEnrollment.status == "approved")
                .where(Event.status == "completed")
                .where(Event.is_test == False)
            )
            user_ids = result.scalars().all()

        logger.info(f"Starting achievement recalculation for {len(user_ids)} users")

        for i, user_id in enumerate(user_ids):
            recalculate_user_achievements.apply_async(
                args=[user_id, send_notifications],
                countdown=i * 2,
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
    from app.models.event import EventEnrollment
    from app.models.fish import Fish

    try:
        with SyncSessionLocal() as db:
            from app.models.user import UserAccount
            user = db.get(UserAccount, user_id)
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
            events_result = db.execute(events_query)
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
            catches_result = db.execute(catches_query)
            catches = catches_result.scalars().all()

            max_length_seen = 0.0

            # Process each catch
            for catch in catches:
                event = db.get(Event, catch.event_id)
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

                awarded = AchievementService.check_and_award_achievements_sync(
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
                event = db.get(Event, event_row.id)
                if not event or not event.event_type:
                    continue

                format_code = "sf" if event.event_type.code == "street_fishing" else "ta"

                awarded = AchievementService.check_and_award_achievements_sync(
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

            # Check Hall of Fame achievements
            hof_awards = _sync_check_hall_of_fame_achievements(db, user_id)
            for ach in hof_awards:
                if ach.code not in new_achievements:
                    new_achievements.append(ach.code)
                    if send_notifications:
                        send_achievement_notification.delay(user_id, ach.id, None)

            db.commit()
            result = {"user_id": user_id, "new_achievements": new_achievements}

        logger.info(f"Recalculated achievements for user {user_id}: {result.get('new_achievements', [])}")
        return result

    except Exception as e:
        logger.error(f"Failed to recalculate achievements for user {user_id}: {e}")
        raise
