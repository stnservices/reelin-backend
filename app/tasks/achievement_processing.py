"""Celery tasks for format-aware achievement processing.

Handles achievement processing after event completion (TA and SF),
with proper format filtering and participant discovery.
Catch-based achievements are also batch-processed here (deferred from per-catch).
"""

import logging
from datetime import timedelta

from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.celery_app import celery_app
from app.database import SyncSessionLocal
from app.models.event import Event
from app.models.catch import Catch, CatchStatus
from app.services.achievement_service import AchievementService
from app.services.statistics_service import StatisticsService
from app.utils.event_formats import get_event_participant_ids_sync

logger = logging.getLogger(__name__)


def _sync_process_format_event_achievements(
    event_id: int,
    format_code: str,
) -> dict:
    """
    Process achievements for all participants of a completed event.

    Args:
        event_id: Event ID
        format_code: Format code ("sf", "ta")

    Returns:
        Dict with processing results
    """
    with SyncSessionLocal() as db:
        try:
            # Get event with type for verification
            event_query = (
                select(Event)
                .options(selectinload(Event.event_type))
                .where(Event.id == event_id)
            )
            event_result = db.execute(event_query)
            event = event_result.scalar_one_or_none()

            if not event:
                logger.warning(f"Event {event_id} not found for achievement processing")
                return {"event_id": event_id, "status": "not_found", "awarded": 0}

            # Get participant IDs
            participant_ids = get_event_participant_ids_sync(db, event_id, format_code)

            logger.info(
                f"Processing achievements for {len(participant_ids)} participants "
                f"in event {event_id} (format: {format_code})"
            )

            total_awarded = 0
            errors = 0

            for user_id in participant_ids:
                try:
                    # Ensure user stats are updated before checking achievements
                    StatisticsService.update_user_stats_for_event_sync(
                        db, user_id, event_id
                    )

                    awarded = AchievementService.check_and_award_achievements_sync(
                        db=db,
                        user_id=user_id,
                        trigger="event_completed",
                        event_id=event_id,
                        format_code=format_code,
                    )
                    total_awarded += len(awarded)

                    if awarded:
                        logger.info(
                            f"User {user_id} earned {len(awarded)} achievements: "
                            f"{[a.code for a in awarded]}"
                        )

                        # Send notifications for newly awarded achievements
                        from app.tasks.achievements import send_achievement_notification
                        for achievement in awarded:
                            send_achievement_notification.delay(
                                user_id, achievement.id, event_id
                            )

                except Exception as e:
                    logger.error(f"Error processing achievements for user {user_id}: {e}")
                    errors += 1
                    continue

            # --- Batch catch-based achievements (deferred from per-catch) ---
            try:
                from app.models.fish import Fish
                catches = db.execute(
                    select(Catch)
                    .options(selectinload(Catch.fish))
                    .where(
                        Catch.event_id == event_id,
                        Catch.status == CatchStatus.APPROVED.value,
                    )
                    .order_by(Catch.submitted_at)
                ).scalars().all()

                # Track personal bests per user during batch
                user_best_length: dict[int, float] = {}

                for catch in catches:
                    try:
                        uid = catch.user_id
                        # Personal best: compare against running max for this user
                        if uid not in user_best_length:
                            prev = db.execute(
                                select(func.max(Catch.length))
                                .where(Catch.user_id == uid)
                                .where(Catch.status == CatchStatus.APPROVED.value)
                                .where(Catch.event_id != event_id)
                            ).scalar() or 0
                            user_best_length[uid] = float(prev)

                        is_pb = float(catch.length or 0) > user_best_length[uid]
                        if is_pb and catch.length:
                            user_best_length[uid] = float(catch.length)

                        context = {
                            "catch_length": catch.length,
                            "catch_weight": catch.weight,
                            "fish_id": catch.fish_id,
                            "fish_slug": catch.fish.slug if catch.fish else None,
                            "is_personal_best": is_pb,
                        }

                        # Early bird / last minute checks
                        catch_time = catch.catch_time or catch.submitted_at
                        if event.start_date and catch_time:
                            context["is_early_bird"] = catch_time <= event.start_date + timedelta(minutes=30)
                        if event.end_date and catch_time:
                            context["is_last_minute"] = catch_time >= event.end_date - timedelta(minutes=30)

                        awarded = AchievementService.check_and_award_achievements_sync(
                            db=db,
                            user_id=uid,
                            trigger="catch_approved",
                            event_id=event_id,
                            catch_id=catch.id,
                            context=context,
                        )
                        total_awarded += len(awarded)

                        if awarded:
                            from app.tasks.achievements import send_achievement_notification
                            for achievement in awarded:
                                send_achievement_notification.delay(uid, achievement.id, event_id)

                    except Exception as e:
                        logger.error(f"Error processing catch {catch.id} achievements: {e}")
                        errors += 1

            except Exception as e:
                logger.error(f"Error batch-processing catch achievements for event {event_id}: {e}")
                errors += 1

            db.commit()

            logger.info(
                f"Event {event_id} achievement processing complete: "
                f"{total_awarded} total awarded, {errors} errors"
            )

            return {
                "event_id": event_id,
                "format_code": format_code,
                "participants": len(participant_ids),
                "awarded": total_awarded,
                "errors": errors,
                "status": "completed",
            }

        except Exception as e:
            logger.error(f"Error in format event achievement processing: {e}")
            db.rollback()
            return {
                "event_id": event_id,
                "status": "error",
                "error": str(e),
                "awarded": 0,
            }


# === Celery Tasks ===


@celery_app.task(name="achievements.process_format_event")
def process_format_event_achievements(event_id: int, format_code: str) -> dict:
    """
    Process achievements for all participants of a TA event.

    Called after event completion (stop action) to check and award
    format-specific achievements for all participants.

    Args:
        event_id: Event ID
        format_code: Format code ("ta")

    Returns:
        Dict with processing results
    """
    try:
        return _sync_process_format_event_achievements(event_id, format_code)
    except Exception as e:
        logger.error(f"Failed to process format event achievements: {e}")
        raise
