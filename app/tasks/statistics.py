"""Celery tasks for statistics operations.

Handles statistics recalculation when events are completed.
"""

import logging
import traceback

from sqlalchemy import select

from app.celery_app import celery_app
from app.database import SyncSessionLocal
from app.models.event import Event, EventStatus
from app.models.trout_area import TALineup
from app.services.statistics_service import StatisticsService

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3)
def recalculate_event_stats(self, event_id: int):
    """
    Recalculate statistics for all participants in a completed event.

    This task is triggered when an event status changes to COMPLETED.
    It recalculates overall statistics for each participant.

    For TA events:
    - Gets all real participants from TALineup (not ghosts)
    - Recalculates stats for each using StatisticsService

    Args:
        event_id: The ID of the completed event
    """
    logger.info(f"Triggering stats recalculation for event {event_id}")

    try:
        return _sync_recalculate_event_stats(event_id)
    except Exception as e:
        logger.error(f"Failed to recalculate stats for event {event_id}: {e}\n{traceback.format_exc()}")
        raise self.retry(exc=e, countdown=30)


def _sync_recalculate_event_stats(event_id: int) -> dict:
    """Sync implementation of event stats recalculation."""
    with SyncSessionLocal() as db:
        # 1. Get the event
        event = db.execute(
            select(Event).where(Event.id == event_id)
        ).scalar_one_or_none()

        if not event:
            logger.warning(f"Event {event_id} not found for stats recalculation")
            return {"error": "Event not found", "event_id": event_id}

        if event.status != EventStatus.COMPLETED.value:
            logger.warning(
                f"Event {event_id} is not completed (status: {event.status}), "
                "skipping stats recalculation"
            )
            return {"error": "Event not completed", "status": event.status}

        # 2. Get unique participants from TA lineups
        user_ids_set = set()

        ta_participants_result = db.execute(
            select(TALineup.user_id)
            .where(TALineup.event_id == event_id)
            .where(TALineup.is_ghost == False)
            .where(TALineup.user_id.isnot(None))
            .distinct()
        )
        for row in ta_participants_result.fetchall():
            user_ids_set.add(row[0])

        user_ids = list(user_ids_set)

        if not user_ids:
            logger.info(f"No participants found for event {event_id}, skipping stats recalculation")
            return {"event_id": event_id, "participant_count": 0, "success_count": 0}

        logger.info(f"Processing stats recalculation for {len(user_ids)} participants in event {event_id}")

        # 3. Recalculate stats for each participant
        success_count = 0
        failure_count = 0

        for user_id in user_ids:
            try:
                StatisticsService.recalculate_all_stats_sync(db, user_id)
                success_count += 1
            except Exception as e:
                logger.error(
                    f"Failed to recalculate stats for user {user_id} "
                    f"in event {event_id}: {e}"
                )
                failure_count += 1

        db.commit()

        logger.info(
            f"Completed stats recalculation for event {event_id}: "
            f"{success_count}/{len(user_ids)} users succeeded, {failure_count} failed"
        )

        return {
            "event_id": event_id,
            "participant_count": len(user_ids),
            "success_count": success_count,
            "failure_count": failure_count,
        }
