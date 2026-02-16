"""Celery tasks for TA leg completion background work.

Defers heavy operations (stats recalculation, Firebase sync) off the
request path to improve validate_opponent_card response times.
Only fires when an entire leg completes (all matches validated).
"""

import logging
import traceback

from app.celery_app import celery_app
from app.database import SyncSessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2)
def ta_leg_completed(self, event_id: int, leg_number: int):
    """
    Background work after a TA leg completes (all matches validated).

    1. Update user stats for ALL competitors in this leg
    2. Sync TA standings to Firebase
    """
    try:
        return _sync_ta_leg_completed(event_id, leg_number)
    except Exception as e:
        logger.error(
            f"ta_leg_completed failed for event {event_id} leg {leg_number}: {e}\n{traceback.format_exc()}"
        )
        raise self.retry(exc=e, countdown=10)


def _sync_ta_leg_completed(event_id: int, leg_number: int) -> dict:
    from app.services.statistics_service import StatisticsService
    from app.services.firebase_leaderboard_service import sync_ta_standings_to_firebase
    from sqlalchemy import select, func
    from app.models.trout_area import (
        TAMatch, TAQualifierStanding, TAEventSettings,
        TAGameCard, TAGameCardStatus,
    )
    from app.models.user import UserProfile

    with SyncSessionLocal() as db:
        results = {"stats_updated": [], "firebase_synced": False, "leg_number": leg_number}

        # 1. Find all unique competitor IDs from matches in this leg
        matches_result = db.execute(
            select(TAMatch.competitor_a_id, TAMatch.competitor_b_id).where(
                TAMatch.event_id == event_id,
                TAMatch.leg_number == leg_number,
            )
        )
        rows = matches_result.all()

        user_ids = set()
        for row in rows:
            if row.competitor_a_id:
                user_ids.add(row.competitor_a_id)
            if row.competitor_b_id:
                user_ids.add(row.competitor_b_id)

        # 2. Update stats for all competitors in this leg
        for user_id in user_ids:
            try:
                StatisticsService.update_user_stats_for_event_sync(db, user_id, event_id)
                results["stats_updated"].append(user_id)
            except Exception as e:
                logger.error(f"Stats update failed for user {user_id}: {e}")

        db.commit()

        # 3. Sync TA standings to Firebase
        try:
            standings_rows = db.execute(
                select(TAQualifierStanding).where(
                    TAQualifierStanding.event_id == event_id
                ).order_by(TAQualifierStanding.rank)
            ).scalars().all()

            if standings_rows:
                all_user_ids = [s.user_id for s in standings_rows]
                profiles = {
                    p.user_id: p for p in db.execute(
                        select(UserProfile).where(UserProfile.user_id.in_(all_user_ids))
                    ).scalars().all()
                }

                settings = db.execute(
                    select(TAEventSettings).where(TAEventSettings.event_id == event_id)
                ).scalar_one_or_none()
                total_legs = settings.number_of_legs if settings else 0
                has_knockout = settings.has_knockout_stage if settings else False

                completed_legs = db.execute(
                    select(func.count(func.distinct(TAGameCard.leg_number))).where(
                        TAGameCard.event_id == event_id,
                        TAGameCard.status == TAGameCardStatus.VALIDATED.value,
                    )
                ).scalar() or 0

                standings_list = []
                for standing in standings_rows:
                    profile = profiles.get(standing.user_id)
                    display_name = profile.full_name if profile else f"User {standing.user_id}"
                    standings_list.append({
                        "rank": standing.rank,
                        "user_id": standing.user_id,
                        "display_name": display_name,
                        "points": float(standing.total_points),
                        "total_catches": standing.total_fish_caught,
                        "victories": standing.total_victories,
                        "ties": (standing.ties_with_fish or 0) + (standing.ties_without_fish or 0),
                        "losses": (standing.losses_with_fish or 0) + (standing.losses_without_fish or 0),
                        "position_change": 0,
                    })

                sync_ta_standings_to_firebase(
                    event_id=event_id,
                    standings=standings_list,
                    current_phase="qualifier",
                    current_leg=leg_number,
                    total_legs=total_legs,
                    completed_legs=completed_legs,
                    has_knockout_bracket=has_knockout,
                )
                results["firebase_synced"] = True

        except Exception as e:
            logger.error(f"Firebase sync failed for event {event_id}: {e}")

        return results
