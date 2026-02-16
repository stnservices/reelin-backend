"""Celery tasks for TA match completion background work.

Defers heavy operations (stats recalculation, Firebase sync) off the
request path to improve validate_opponent_card response times.
"""

import asyncio
import logging
import traceback

from app.celery_app import celery_app
from app.database import CelerySessionContext

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2)
def ta_match_completed(self, event_id: int, competitor_a_id: int, competitor_b_id: int):
    """
    Background work after a TA match completes.

    1. Update user stats for both competitors
    2. Sync TA standings to Firebase
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                _async_ta_match_completed(event_id, competitor_a_id, competitor_b_id)
            )
        finally:
            loop.close()
    except Exception as e:
        logger.error(
            f"ta_match_completed failed for event {event_id}: {e}\n{traceback.format_exc()}"
        )
        raise self.retry(exc=e, countdown=10)


async def _async_ta_match_completed(
    event_id: int, competitor_a_id: int, competitor_b_id: int
) -> dict:
    from app.services.statistics_service import statistics_service
    from app.services.firebase_leaderboard_service import sync_ta_standings_to_firebase
    from sqlalchemy import select, func
    from app.models.trout_area import (
        TAQualifierStanding, TAEventSettings, TAGameCard, TAGameCardStatus,
    )
    from app.models.user import UserProfile

    async with CelerySessionContext() as session_maker:
        async with session_maker() as db:
            results = {"stats_updated": [], "firebase_synced": False}

            # 1. Update stats for both competitors
            for user_id in [competitor_a_id, competitor_b_id]:
                if user_id:
                    try:
                        await statistics_service.update_user_stats_for_event(
                            db, user_id, event_id
                        )
                        results["stats_updated"].append(user_id)
                    except Exception as e:
                        logger.error(f"Stats update failed for user {user_id}: {e}")

            # 2. Sync TA standings to Firebase (with fixed N+1 query)
            try:
                standings_query = select(TAQualifierStanding).where(
                    TAQualifierStanding.event_id == event_id
                ).order_by(TAQualifierStanding.rank)
                standings_rows = (await db.execute(standings_query)).scalars().all()

                if standings_rows:
                    # Batch-load all profiles in one query
                    user_ids = [s.user_id for s in standings_rows]
                    profiles_result = await db.execute(
                        select(UserProfile).where(UserProfile.user_id.in_(user_ids))
                    )
                    profiles = {p.user_id: p for p in profiles_result.scalars().all()}

                    settings_result = await db.execute(
                        select(TAEventSettings).where(TAEventSettings.event_id == event_id)
                    )
                    settings = settings_result.scalar_one_or_none()
                    total_legs = settings.number_of_legs if settings else 0
                    has_knockout = settings.has_knockout_stage if settings else False

                    completed_result = await db.execute(
                        select(func.count(func.distinct(TAGameCard.leg_number))).where(
                            TAGameCard.event_id == event_id,
                            TAGameCard.status == TAGameCardStatus.VALIDATED.value,
                        )
                    )
                    completed_legs = completed_result.scalar() or 0

                    current_leg_result = await db.execute(
                        select(func.max(TAGameCard.leg_number)).where(
                            TAGameCard.event_id == event_id,
                        )
                    )
                    current_leg = current_leg_result.scalar() or 1

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
                        current_leg=current_leg,
                        total_legs=total_legs,
                        completed_legs=completed_legs,
                        has_knockout_bracket=has_knockout,
                    )
                    results["firebase_synced"] = True

            except Exception as e:
                logger.error(f"Firebase sync failed for event {event_id}: {e}")

            return results
