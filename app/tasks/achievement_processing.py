"""Celery tasks for format-aware achievement processing.

Handles achievement processing after TA event completion,
with proper format filtering and participant discovery.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.celery_app import celery_app
from app.database import SyncSessionLocal
from app.models.event import Event
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
