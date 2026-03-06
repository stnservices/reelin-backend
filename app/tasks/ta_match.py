"""Celery tasks for TA leg completion background work.

Defers heavy operations (standings rebuild + Firebase sync) off the request path.
Only fires when an entire leg completes (all matches validated).

Standings are NOT written during individual match completions — they are computed
on-the-fly for GET requests. This task rebuilds the standings table once per leg
for Firebase sync and statistics.
"""

import logging
import traceback
from decimal import Decimal

from app.celery_app import celery_app
from app.database import SyncSessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2)
def ta_leg_completed(self, event_id: int, leg_number: int):
    """
    Background work after a TA leg completes (all matches validated).

    Rebuilds standings table and syncs to Firebase for real-time web updates.
    """
    try:
        return _sync_ta_leg_completed(event_id, leg_number)
    except Exception as e:
        logger.error(
            f"ta_leg_completed failed for event {event_id} leg {leg_number}: {e}\n{traceback.format_exc()}"
        )
        raise self.retry(exc=e, countdown=10)


def _rebuild_standings_sync(db, event_id: int) -> None:
    """Rebuild TAQualifierStanding table from completed matches (sync version for Celery)."""
    from sqlalchemy import select, func
    from app.models.trout_area import (
        TAQualifierStanding, TAMatch, TAMatchStatus, TAEventSettings,
    )
    from app.models.enrollment import EventEnrollment

    # Get all completed qualifier matches
    matches = db.execute(
        select(TAMatch).where(
            TAMatch.event_id == event_id,
            TAMatch.status == TAMatchStatus.COMPLETED.value,
            TAMatch.phase == "qualifier",
        )
    ).scalars().all()

    # Get user->enrollment mapping
    enrollments = db.execute(
        select(EventEnrollment).where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.status == "approved",
        )
    ).scalars().all()
    user_enrollment_map = {e.user_id: e.id for e in enrollments}

    # Clear existing standings
    db.execute(
        TAQualifierStanding.__table__.delete().where(TAQualifierStanding.event_id == event_id)
    )

    # Accumulate stats by user
    user_stats = {}

    def _init_stats():
        return {
            "total_points": Decimal("0"),
            "total_fish_caught": 0,
            "total_matches": 0,
            "total_victories": 0,
            "total_ties": 0,
            "total_losses": 0,
            "ties_with_fish": 0,
            "ties_without_fish": 0,
            "losses_with_fish": 0,
            "losses_without_fish": 0,
            "leg_results": {},
        }

    def _accumulate(stats, catches, outcome_code, points, leg_num):
        stats["total_points"] += points or Decimal("0")
        stats["total_fish_caught"] += catches or 0
        stats["total_matches"] += 1

        if outcome_code == "V":
            stats["total_victories"] += 1
        elif outcome_code == "T":
            stats["total_ties"] += 1
            stats["ties_with_fish"] += 1
        elif outcome_code == "T0":
            stats["total_ties"] += 1
            stats["ties_without_fish"] += 1
        elif outcome_code == "L":
            stats["total_losses"] += 1
            stats["losses_with_fish"] += 1
        elif outcome_code == "L0":
            stats["total_losses"] += 1
            stats["losses_without_fish"] += 1

        leg_key = str(leg_num)
        if leg_key not in stats["leg_results"]:
            stats["leg_results"][leg_key] = {
                "points": 0, "victories": 0, "ties": 0, "losses": 0, "fish": 0,
            }
        leg_data = stats["leg_results"][leg_key]
        leg_data["points"] = float(leg_data["points"]) + float(points or 0)
        leg_data["fish"] = leg_data["fish"] + (catches or 0)
        if outcome_code == "V":
            leg_data["victories"] += 1
        elif outcome_code in ["T", "T0"]:
            leg_data["ties"] += 1
        elif outcome_code in ["L", "L0"]:
            leg_data["losses"] += 1

    for match in matches:
        if match.competitor_a_id:
            if match.competitor_a_id not in user_stats:
                user_stats[match.competitor_a_id] = _init_stats()
            _accumulate(
                user_stats[match.competitor_a_id],
                match.competitor_a_catches,
                match.competitor_a_outcome_code,
                match.competitor_a_points,
                match.leg_number,
            )
        if match.competitor_b_id:
            if match.competitor_b_id not in user_stats:
                user_stats[match.competitor_b_id] = _init_stats()
            _accumulate(
                user_stats[match.competitor_b_id],
                match.competitor_b_catches,
                match.competitor_b_outcome_code,
                match.competitor_b_points,
                match.leg_number,
            )

    # Create standing records
    for user_id, stats in user_stats.items():
        enrollment_id = user_enrollment_map.get(user_id)
        if not enrollment_id:
            continue
        standing = TAQualifierStanding(
            event_id=event_id,
            user_id=user_id,
            enrollment_id=enrollment_id,
            rank=0,
            total_points=stats["total_points"],
            total_fish_caught=stats["total_fish_caught"],
            total_matches=stats["total_matches"],
            total_victories=stats["total_victories"],
            total_ties=stats["total_ties"],
            total_losses=stats["total_losses"],
            ties_with_fish=stats["ties_with_fish"],
            ties_without_fish=stats["ties_without_fish"],
            losses_with_fish=stats["losses_with_fish"],
            losses_without_fish=stats["losses_without_fish"],
            leg_results=stats["leg_results"],
        )
        db.add(standing)

    db.flush()

    # Recalculate ranks (sync version of recalculate_event_ranks)
    standings = list(db.execute(
        select(TAQualifierStanding).where(TAQualifierStanding.event_id == event_id)
    ).scalars().all())

    if not standings:
        return

    standings.sort(key=lambda s: (
        -float(s.total_points),
        -s.total_fish_caught,
        -s.total_victories,
        -s.ties_with_fish,
        -s.ties_without_fish,
        s.losses_with_fish,
        s.losses_without_fish,
    ))

    current_rank = 1
    for i, standing in enumerate(standings):
        if i > 0:
            prev = standings[i - 1]
            if (standing.total_points == prev.total_points and
                standing.total_fish_caught == prev.total_fish_caught and
                standing.total_victories == prev.total_victories):
                pass
            else:
                current_rank = i + 1
        standing.rank = current_rank

    # Update knockout qualification
    settings = db.execute(
        select(TAEventSettings).where(TAEventSettings.event_id == event_id)
    ).scalar_one_or_none()
    knockout_qualifiers = settings.knockout_qualifiers if settings else 4
    for standing in standings:
        standing.qualifies_for_knockout = standing.rank <= knockout_qualifiers

    db.flush()


def _sync_ta_leg_completed(event_id: int, leg_number: int) -> dict:
    from app.services.firebase_leaderboard_service import sync_ta_standings_to_firebase
    from sqlalchemy import select, func
    from app.models.trout_area import (
        TAQualifierStanding, TAEventSettings,
        TAGameCard, TAGameCardStatus,
    )
    from app.models.user import UserProfile

    with SyncSessionLocal() as db:
        results = {"firebase_synced": False, "standings_rebuilt": False, "leg_number": leg_number}

        # Rebuild standings table (single write per leg, no race conditions)
        try:
            _rebuild_standings_sync(db, event_id)
            db.commit()
            results["standings_rebuilt"] = True
        except Exception as e:
            logger.error(f"Standings rebuild failed for event {event_id}: {e}\n{traceback.format_exc()}")
            db.rollback()

        # Sync TA standings to Firebase
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
