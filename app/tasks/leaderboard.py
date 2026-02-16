"""Celery tasks for leaderboard notifications and rank updates.

This task is triggered after catch validation to:
1. Update EventScoreboard ranks in the database
2. Detect ranking movements
3. Notify SSE clients via Redis pub/sub

The main leaderboard calculation happens in the API endpoint (api/v1/leaderboard.py).
This task ensures DB ranks are updated and clients are notified of changes.
"""

import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Optional

from celery import shared_task
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.celery_app import celery_app
from app.database import SyncSessionLocal
from app.models.catch import Catch, CatchStatus, RankingMovement, EventScoreboard
from app.models.event import Event, EventFishScoring, EventSpeciesBonusPoints
from app.models.fish import Fish
from app.models.team import Team, TeamMember
from app.models.user import UserAccount
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.services.firebase_leaderboard_service import sync_leaderboard_to_firebase

logger = logging.getLogger(__name__)


def get_initials(name: str) -> str:
    """Get initials from a full name (e.g., 'John Doe' -> 'J.D.')"""
    if not name:
        return ""
    name_parts = re.split(r'[\s\-_]+', name)
    initials = '.'.join([part[0].upper() for part in name_parts if part])
    return initials + '.' if initials else ""


@celery_app.task(bind=True, max_retries=3)
def recalculate_event_leaderboard(self, event_id: int, triggered_by: str = "unknown"):
    """
    Celery task to update leaderboard ranks and notify clients.

    Triggered by catch validation events. This task:
    1. Calculates current rankings
    2. Updates EventScoreboard.rank in database
    3. Detects and records ranking movements
    4. Publishes SSE notification for live clients

    Note: Main leaderboard data is always calculated fresh from DB in the API.
    This task ensures ranks are persisted and clients are notified.
    """
    logger.info(f"Recalculating leaderboard for event {event_id} (triggered by: {triggered_by})")

    try:
        return _sync_recalculate(event_id)
    except Exception as e:
        logger.error(f"Failed to recalculate leaderboard for event {event_id}: {e}")
        raise self.retry(exc=e, countdown=5)


def _sync_recalculate(event_id: int) -> dict:
    """Sync implementation of leaderboard recalculation."""
    with SyncSessionLocal() as db:
        # Load event with scoring config
        event_query = (
            select(Event)
            .options(selectinload(Event.scoring_config))
            .where(Event.id == event_id)
        )
        result = db.execute(event_query)
        event = result.scalar_one_or_none()

        if not event:
            logger.warning(f"Event {event_id} not found")
            return {"error": "Event not found"}

        # Load fish scoring config
        fish_scoring_query = (
            select(EventFishScoring)
            .options(selectinload(EventFishScoring.fish))
            .where(EventFishScoring.event_id == event_id)
        )
        fish_scoring_result = db.execute(fish_scoring_query)
        fish_scoring_list = fish_scoring_result.scalars().all()
        fish_scoring = {fs.fish_id: fs for fs in fish_scoring_list}

        # Load bonus points config
        bonus_query = (
            select(EventSpeciesBonusPoints)
            .where(EventSpeciesBonusPoints.event_id == event_id)
            .order_by(EventSpeciesBonusPoints.species_count.desc())
        )
        bonus_result = db.execute(bonus_query)
        bonus_points_config = bonus_result.scalars().all()

        # Get scoring config
        scoring_code = event.scoring_config.code if event.scoring_config else "top_x_by_species"
        default_top_x = event.scoring_config.default_top_x if event.scoring_config else 10
        top_x_overall = event.top_x_overall or default_top_x or 10

        # Load all approved catches
        approved_catches_query = (
            select(Catch)
            .options(
                selectinload(Catch.user).selectinload(UserAccount.profile),
                selectinload(Catch.fish),
            )
            .where(
                Catch.event_id == event_id,
                Catch.status == CatchStatus.APPROVED.value,
            )
            .order_by(Catch.length.desc())
        )
        catches_result = db.execute(approved_catches_query)
        approved_catches = catches_result.scalars().unique().all()

        # Load rejected catches
        rejected_catches_query = (
            select(Catch)
            .options(
                selectinload(Catch.user).selectinload(UserAccount.profile),
                selectinload(Catch.fish),
                selectinload(Catch.validated_by).selectinload(UserAccount.profile),
            )
            .where(
                Catch.event_id == event_id,
                Catch.status == CatchStatus.REJECTED.value,
            )
            .order_by(Catch.submitted_at.desc())
        )
        rejected_result = db.execute(rejected_catches_query)
        rejected_catches = rejected_result.scalars().unique().all()

        # Get disqualified users for this event
        disqualified_query = select(EventEnrollment).where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.status == EnrollmentStatus.DISQUALIFIED.value,
        )
        disqualified_result = db.execute(disqualified_query)
        disqualified_enrollments = disqualified_result.scalars().all()
        disqualified_user_ids = {e.user_id for e in disqualified_enrollments}
        disqualified_info = {
            e.user_id: {
                "reason": e.disqualification_reason,
                "at": e.disqualified_at,
            }
            for e in disqualified_enrollments
        }

        # Get old rankings from EventScoreboard (DB is source of truth)
        old_rankings = {}
        scoreboard_query = select(EventScoreboard).where(
            EventScoreboard.event_id == event_id
        )
        scoreboard_result = db.execute(scoreboard_query)
        scoreboards = scoreboard_result.scalars().all()
        for sb in scoreboards:
            old_rankings[sb.user_id] = sb.rank

        # Calculate based on event type
        if event.is_team_event:
            result = _calculate_team_leaderboard(
                db, event, approved_catches, rejected_catches,
                fish_scoring, bonus_points_config, scoring_code, top_x_overall,
                disqualified_user_ids, disqualified_info
            )
        else:
            result = _calculate_individual_leaderboard(
                db, event, approved_catches, rejected_catches,
                fish_scoring, bonus_points_config, scoring_code, top_x_overall,
                disqualified_user_ids, disqualified_info
            )

        # Detect and record ranking movements (skip DQ entries with rank=None)
        movements = []
        for entry in result["leaderboard"]["entries"]:
            if entry.get("is_disqualified"):
                continue

            entity_id = entry.get("team_id") if event.is_team_event else entry.get("user_id")
            new_rank = entry.get("rank")
            old_rank = old_rankings.get(entity_id)

            if old_rank is not None and new_rank is not None and old_rank != new_rank:
                movement = {
                    "event_id": event_id,
                    "team_id": entity_id if event.is_team_event else None,
                    "user_id": entity_id if not event.is_team_event else None,
                    "old_rank": old_rank,
                    "new_rank": new_rank,
                    "movement": old_rank - new_rank,
                    "created_at": datetime.utcnow().isoformat(),
                }
                if event.is_team_event:
                    movement["team_name"] = entry.get("team_name")
                else:
                    movement["user_name"] = entry.get("user_name")
                movements.append(movement)

        # Update EventScoreboard ranks in DB (source of truth)
        for entry in result["leaderboard"]["entries"]:
            if entry.get("is_disqualified"):
                continue
            user_id = entry.get("user_id")
            new_rank = entry.get("rank")
            old_rank = old_rankings.get(user_id)
            if user_id and new_rank is not None:
                db.execute(
                    update(EventScoreboard)
                    .where(
                        EventScoreboard.event_id == event_id,
                        EventScoreboard.user_id == user_id,
                    )
                    .values(rank=new_rank, previous_rank=old_rank)
                )

        # Save movements to database
        if movements:
            for m in movements:
                db_movement = RankingMovement(
                    event_id=event_id,
                    user_id=m.get("user_id"),
                    team_id=m.get("team_id"),
                    old_rank=m["old_rank"],
                    new_rank=m["new_rank"],
                    catch_id=m.get("catch_id"),
                )
                db.add(db_movement)

        db.commit()

        # Build recent catches list for Firebase
        recent_catches = []
        sorted_by_validated = sorted(
            approved_catches,
            key=lambda c: c.validated_at or c.submitted_at,
            reverse=True
        )[:20]
        for catch in sorted_by_validated:
            user_name = ""
            if catch.user and catch.user.profile:
                user_name = f"{catch.user.profile.first_name} {catch.user.profile.last_name}"
            recent_catches.append({
                "catch_id": catch.id,
                "fish_name": catch.fish.name if catch.fish else "Unknown",
                "length": catch.length,
                "points": int(catch.length),
                "angler_name": user_name,
                "validated_at_ms": int(catch.validated_at.timestamp() * 1000) if catch.validated_at else None,
                "is_scored": True,
            })

        # Sync to Firebase for web real-time updates
        sync_leaderboard_to_firebase(
            event_id=event_id,
            leaderboard_data=result["leaderboard"],
            movements=movements[-10:] if movements else [],
            recent_catches=recent_catches,
        )

        logger.info(
            f"Leaderboard recalculated for event {event_id}: "
            f"{len(result['leaderboard']['entries'])} entries, {len(movements)} movements"
        )

        return {
            "event_id": event_id,
            "entries_count": len(result["leaderboard"]["entries"]),
            "movements_count": len(movements),
        }


def _calculate_individual_leaderboard(
    db,
    event: Event,
    approved_catches: list[Catch],
    rejected_catches: list[Catch],
    fish_scoring: dict[int, EventFishScoring],
    bonus_config: list[EventSpeciesBonusPoints],
    scoring_code: str,
    top_x_overall: int,
    disqualified_user_ids: set,
    disqualified_info: dict,
) -> dict:
    """Calculate leaderboard for individual events."""

    # Group catches by user (excluding disqualified from rankings)
    user_catches: dict[int, list[Catch]] = defaultdict(list)
    disqualified_catches: dict[int, list[Catch]] = defaultdict(list)
    for catch in approved_catches:
        if catch.user_id in disqualified_user_ids:
            disqualified_catches[catch.user_id].append(catch)
        else:
            user_catches[catch.user_id].append(catch)

    # Group rejected catches by user
    user_rejected: dict[int, list[Catch]] = defaultdict(list)
    for catch in rejected_catches:
        user_rejected[catch.user_id].append(catch)

    # Calculate scores for each user
    user_scores = []
    user_details = {}

    for user_id, catches in user_catches.items():
        catches_sorted = sorted(catches, key=lambda c: c.length, reverse=True)

        user = catches_sorted[0].user
        user_name = f"{user.profile.first_name} {user.profile.last_name}" if user.profile else f"User {user_id}"

        catch_details = _calculate_catch_details(
            catches_sorted, fish_scoring, scoring_code, top_x_overall
        )

        scored_catches = [c for c in catch_details if c["is_scored"]]
        total_points = sum(c["points"] for c in scored_catches)
        species_ids = set(c["fish_id"] for c in scored_catches)

        species_bonus = 0
        for bp in bonus_config:
            if len(species_ids) >= bp.species_count:
                species_bonus = bp.bonus_points
                break

        total_points += species_bonus

        best_length = max((c["length"] for c in scored_catches), default=0)
        best_catch = max(scored_catches, key=lambda c: c["length"]) if scored_catches else None
        best_catch_species = best_catch["fish_name"] if best_catch else None
        total_length = sum(c["length"] for c in scored_catches)
        avg_catch = round(total_length / len(scored_catches), 2) if scored_catches else 0.0
        first_catch_time = min(
            (c["submitted_at"] for c in catch_details),
            default=None
        )

        entry = {
            "user_id": user_id,
            "user_name": user_name,
            "avatar_url": user.profile.profile_picture_url if user.profile else None,
            "total_points": total_points,
            "total_catches": len(catches),
            "counted_catches": len(scored_catches),
            "species_count": len(species_ids),
            "species_bonus": species_bonus,
            "best_catch_length": best_length,
            "best_catch_species": best_catch_species,
            "best_catch_photo_url": best_catch["photo_url"] if best_catch else None,
            "best_catch_user_name": user_name,
            "average_catch": avg_catch,
            "first_catch_time": first_catch_time,
            "is_disqualified": False,
        }
        user_scores.append(entry)

        rejected_list = _build_rejected_details(user_rejected.get(user_id, []))
        user_details[user_id] = {
            "user_id": user_id,
            "user_name": user_name,
            "is_team_event": False,
            "total_catches": len(catches),
            "scored_catches": len(scored_catches),
            "not_scored_catches": len(catches) - len(scored_catches),
            "total_points": total_points,
            "bonus_points": species_bonus,
            "species_count": len(species_ids),
            "catches": catch_details,
            "rejected_catches": rejected_list,
        }

    # Sort with 6-level tiebreaker
    user_scores.sort(key=lambda x: (
        -x["total_points"],
        -x["counted_catches"],
        -x["species_count"],
        -x["best_catch_length"],
        -x["average_catch"],
        x["first_catch_time"] or datetime.max,
    ))

    # Assign ranks with tie handling
    current_rank = 1
    prev_entry = None
    for i, entry in enumerate(user_scores, start=1):
        if prev_entry and _entries_are_tied(entry, prev_entry):
            entry["rank"] = current_rank
        else:
            current_rank = i
            entry["rank"] = current_rank
        prev_entry = entry

    # Build disqualified entries (shown at bottom, unranked)
    disqualified_entries = []
    for user_id, catches in disqualified_catches.items():
        if not catches:
            continue
        catches_sorted = sorted(catches, key=lambda c: c.length, reverse=True)
        user = catches_sorted[0].user
        user_name = f"{user.profile.first_name} {user.profile.last_name}" if user.profile else f"User {user_id}"

        catch_details = _calculate_catch_details(
            catches_sorted, fish_scoring, scoring_code, top_x_overall
        )

        species_ids = set(c["fish_id"] for c in catch_details)
        total_points = sum(c["points"] for c in catch_details)
        best_length = max((c["length"] for c in catch_details), default=0)
        best_catch = max(catch_details, key=lambda c: c["length"]) if catch_details else None
        best_catch_species = best_catch["fish_name"] if best_catch else None
        total_length = sum(c["length"] for c in catch_details)
        avg_catch = round(total_length / len(catch_details), 2) if catch_details else 0.0

        dq_info = disqualified_info.get(user_id, {})

        entry = {
            "user_id": user_id,
            "user_name": user_name,
            "avatar_url": user.profile.profile_picture_url if user.profile else None,
            "total_points": total_points,
            "total_catches": len(catches),
            "counted_catches": 0,
            "species_count": len(species_ids),
            "species_bonus": 0,
            "best_catch_length": best_length,
            "best_catch_species": best_catch_species,
            "best_catch_photo_url": best_catch["photo_url"] if best_catch else None,
            "best_catch_user_name": user_name,
            "average_catch": avg_catch,
            "first_catch_time": None,
            "rank": None,
            "is_disqualified": True,
            "disqualification_reason": dq_info.get("reason"),
            "disqualified_at": dq_info.get("at").isoformat() if dq_info.get("at") else None,
        }
        disqualified_entries.append(entry)

        rejected_list = _build_rejected_details(user_rejected.get(user_id, []))
        user_details[user_id] = {
            "user_id": user_id,
            "user_name": user_name,
            "is_team_event": False,
            "total_catches": len(catches),
            "scored_catches": 0,
            "not_scored_catches": len(catches),
            "total_points": total_points,
            "bonus_points": 0,
            "species_count": len(species_ids),
            "catches": catch_details,
            "rejected_catches": rejected_list,
            "is_disqualified": True,
            "disqualification_reason": dq_info.get("reason"),
        }

    # For completed events, include enrolled users with 0 catches
    no_catch_entries = []
    if event.is_completed:
        users_with_activity = set(user_catches.keys()) | set(disqualified_catches.keys())

        enrolled_query = (
            select(EventEnrollment)
            .options(selectinload(EventEnrollment.user).selectinload(UserAccount.profile))
            .where(
                EventEnrollment.event_id == event.id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
                EventEnrollment.user_id.notin_(users_with_activity) if users_with_activity else True,
            )
        )
        enrolled_result = db.execute(enrolled_query)
        enrolled_no_catches = enrolled_result.scalars().unique().all()

        last_rank = user_scores[-1]["rank"] if user_scores else 0
        shared_rank = last_rank + 1

        for enrollment in enrolled_no_catches:
            user = enrollment.user
            user_id = user.id
            user_name = f"{user.profile.first_name} {user.profile.last_name}" if user.profile else f"User {user_id}"

            entry = {
                "user_id": user_id,
                "user_name": user_name,
                "avatar_url": user.profile.profile_picture_url if user.profile else None,
                "total_points": 0,
                "total_catches": 0,
                "counted_catches": 0,
                "species_count": 0,
                "species_bonus": 0,
                "best_catch_length": 0,
                "best_catch_species": None,
                "best_catch_photo_url": None,
                "best_catch_user_name": None,
                "average_catch": 0.0,
                "first_catch_time": None,
                "rank": shared_rank,
                "is_disqualified": False,
                "has_no_catches": True,
            }
            no_catch_entries.append(entry)

            user_details[user_id] = {
                "user_id": user_id,
                "user_name": user_name,
                "is_team_event": False,
                "total_catches": 0,
                "scored_catches": 0,
                "not_scored_catches": 0,
                "total_points": 0,
                "bonus_points": 0,
                "species_count": 0,
                "catches": [],
                "rejected_catches": [],
                "has_no_catches": True,
            }

    # Combine ranked entries + no-catch entries + disqualified at bottom
    all_entries = user_scores + no_catch_entries + disqualified_entries

    last_updated = max(
        (c.validated_at or c.submitted_at for c in approved_catches),
        default=datetime.utcnow()
    )

    dq_catch_count = sum(len(catches) for catches in disqualified_catches.values())

    leaderboard = {
        "event_id": event.id,
        "event_name": event.name,
        "is_team_event": False,
        "scoring_type": scoring_code,
        "entries": all_entries,
        "total_participants": len(user_scores),
        "total_catches": len(approved_catches) - dq_catch_count,
        "disqualified_count": len(disqualified_entries),
        "no_catch_participants_count": len(no_catch_entries),
        "last_updated": last_updated.isoformat() if last_updated else None,
    }

    return {
        "leaderboard": leaderboard,
        "user_details": user_details,
        "team_details": {},
    }


def _calculate_team_leaderboard(
    db,
    event: Event,
    approved_catches: list[Catch],
    rejected_catches: list[Catch],
    fish_scoring: dict[int, EventFishScoring],
    bonus_config: list[EventSpeciesBonusPoints],
    scoring_code: str,
    top_x_overall: int,
    disqualified_user_ids: set,
    disqualified_info: dict,
) -> dict:
    """Calculate leaderboard for team events."""

    # Load teams
    teams_query = (
        select(Team)
        .options(selectinload(Team.members).selectinload(TeamMember.enrollment))
        .where(Team.event_id == event.id, Team.is_active == True)
    )
    teams_result = db.execute(teams_query)
    teams = teams_result.scalars().unique().all()

    # Build user -> team mapping
    user_to_team: dict[int, int] = {}
    team_info: dict[int, dict] = {}

    for team in teams:
        team_info[team.id] = {
            "name": team.name,
            "logo_url": team.logo_url,
            "members": [],
        }
        for member in team.members:
            if member.is_active and member.enrollment:
                user_id = member.enrollment.user_id
                user_to_team[user_id] = team.id
                is_dq = user_id in disqualified_user_ids
                dq_info = disqualified_info.get(user_id, {})
                team_info[team.id]["members"].append({
                    "user_id": user_id,
                    "role": member.role,
                    "is_disqualified": is_dq,
                    "disqualification_reason": dq_info.get("reason") if is_dq else None,
                })

    # Group catches by team
    team_catches: dict[int, list[Catch]] = defaultdict(list)
    disqualified_catch_count: dict[int, int] = defaultdict(int)
    for catch in approved_catches:
        team_id = user_to_team.get(catch.user_id)
        if team_id:
            if catch.user_id in disqualified_user_ids:
                disqualified_catch_count[team_id] += 1
                continue
            team_catches[team_id].append(catch)

    # Group rejected catches by team
    team_rejected: dict[int, list[Catch]] = defaultdict(list)
    for catch in rejected_catches:
        team_id = user_to_team.get(catch.user_id)
        if team_id:
            team_rejected[team_id].append(catch)

    # Calculate scores for each team
    team_scores = []
    team_details = {}

    for team_id, catches in team_catches.items():
        catches_sorted = sorted(catches, key=lambda c: c.length, reverse=True)
        info = team_info[team_id]

        catch_details = _calculate_catch_details(
            catches_sorted, fish_scoring, scoring_code, top_x_overall, is_team=True
        )

        scored_catches = [c for c in catch_details if c["is_scored"]]
        total_points = sum(c["points"] for c in scored_catches)
        species_ids = set(c["fish_id"] for c in scored_catches)

        species_bonus = 0
        for bp in bonus_config:
            if len(species_ids) >= bp.species_count:
                species_bonus = bp.bonus_points
                break

        total_points += species_bonus

        best_length = max((c["length"] for c in scored_catches), default=0)
        best_catch = max(scored_catches, key=lambda c: c["length"]) if scored_catches else None
        best_catch_species = best_catch["fish_name"] if best_catch else None
        total_length = sum(c["length"] for c in scored_catches)
        avg_catch = round(total_length / len(scored_catches), 2) if scored_catches else 0.0
        first_catch_time = min(
            (c["submitted_at"] for c in catch_details),
            default=None
        )

        member_stats: dict[int, dict] = {}
        for c in catch_details:
            uid = c["uploaded_by_id"]
            if uid not in member_stats:
                is_dq = uid in disqualified_user_ids
                dq_info = disqualified_info.get(uid, {})
                member_stats[uid] = {
                    "user_id": uid,
                    "user_name": c["uploaded_by_name"],
                    "initials": c["uploaded_by_initials"],
                    "total_uploaded": 0,
                    "total_scoring": 0,
                    "total_rejected": 0,
                    "is_disqualified": is_dq,
                    "disqualification_reason": dq_info.get("reason") if is_dq else None,
                }
            member_stats[uid]["total_uploaded"] += 1
            if c["is_scored"]:
                member_stats[uid]["total_scoring"] += 1

        for c in team_rejected.get(team_id, []):
            uid = c.user_id
            if uid not in member_stats:
                user_name = f"{c.user.profile.first_name} {c.user.profile.last_name}" if c.user.profile else f"User {uid}"
                is_dq = uid in disqualified_user_ids
                dq_info = disqualified_info.get(uid, {})
                member_stats[uid] = {
                    "user_id": uid,
                    "user_name": user_name,
                    "initials": get_initials(user_name),
                    "total_uploaded": 0,
                    "total_scoring": 0,
                    "total_rejected": 0,
                    "is_disqualified": is_dq,
                    "disqualification_reason": dq_info.get("reason") if is_dq else None,
                }
            member_stats[uid]["total_rejected"] += 1

        dq_members_in_team = [m for m in info["members"] if m.get("is_disqualified")]
        dq_catches_excluded = disqualified_catch_count.get(team_id, 0)

        entry = {
            "team_id": team_id,
            "team_name": info["name"],
            "team_logo_url": info["logo_url"],
            "total_points": total_points,
            "total_catches": len(catches),
            "counted_catches": len(scored_catches),
            "species_count": len(species_ids),
            "species_bonus": species_bonus,
            "best_catch_length": best_length,
            "best_catch_species": best_catch_species,
            "best_catch_photo_url": best_catch["photo_url"] if best_catch else None,
            "best_catch_user_name": best_catch["uploaded_by_name"] if best_catch else None,
            "average_catch": avg_catch,
            "first_catch_time": first_catch_time,
            "member_count": len(info["members"]),
            "disqualified_members_count": len(dq_members_in_team),
            "disqualified_catches_excluded": dq_catches_excluded,
        }
        team_scores.append(entry)

        rejected_list = _build_rejected_details(team_rejected.get(team_id, []), is_team=True)
        team_details[team_id] = {
            "team_id": team_id,
            "team_name": info["name"],
            "is_team_event": True,
            "total_catches": len(catches),
            "scored_catches": len(scored_catches),
            "not_scored_catches": len(catches) - len(scored_catches),
            "total_points": total_points,
            "bonus_points": species_bonus,
            "species_count": len(species_ids),
            "catches": catch_details,
            "rejected_catches": rejected_list,
            "team_members": list(member_stats.values()),
        }

    # Sort teams with 6-level tiebreaker
    team_scores.sort(key=lambda x: (
        -x["total_points"],
        -x["counted_catches"],
        -x["species_count"],
        -x["best_catch_length"],
        -x["average_catch"],
        x["first_catch_time"] or datetime.max,
    ))

    # Assign ranks with tie handling
    current_rank = 1
    prev_entry = None
    for i, entry in enumerate(team_scores, start=1):
        if prev_entry and _entries_are_tied(entry, prev_entry):
            entry["rank"] = current_rank
        else:
            current_rank = i
            entry["rank"] = current_rank
        prev_entry = entry

    # For completed events, include teams with no catches
    no_catch_teams = []
    if event.is_completed:
        last_rank = team_scores[-1]["rank"] if team_scores else 0
        shared_rank = last_rank + 1

        for team_id, info in team_info.items():
            if team_id not in team_catches:
                dq_members_in_team = [m for m in info["members"] if m.get("is_disqualified")]
                dq_catches_excluded = disqualified_catch_count.get(team_id, 0)
                no_catch_teams.append({
                    "team_id": team_id,
                    "team_name": info["name"],
                    "team_logo_url": info["logo_url"],
                    "total_points": 0,
                    "total_catches": 0,
                    "counted_catches": 0,
                    "species_count": 0,
                    "species_bonus": 0,
                    "best_catch_length": 0,
                    "best_catch_species": None,
                    "best_catch_photo_url": None,
                    "best_catch_user_name": None,
                    "average_catch": 0.0,
                    "first_catch_time": None,
                    "member_count": len(info["members"]),
                    "disqualified_members_count": len(dq_members_in_team),
                    "disqualified_catches_excluded": dq_catches_excluded,
                    "rank": shared_rank,
                    "has_no_catches": True,
                })
                team_details[team_id] = {
                    "team_id": team_id,
                    "team_name": info["name"],
                    "is_team_event": True,
                    "total_catches": 0,
                    "scored_catches": 0,
                    "not_scored_catches": 0,
                    "total_points": 0,
                    "bonus_points": 0,
                    "species_count": 0,
                    "catches": [],
                    "rejected_catches": [],
                    "team_members": [],
                    "has_no_catches": True,
                }

    all_team_entries = team_scores + no_catch_teams

    last_updated = max(
        (c.validated_at or c.submitted_at for c in approved_catches),
        default=datetime.utcnow()
    )

    total_dq_catches = sum(disqualified_catch_count.values())
    total_dq_users = len(disqualified_user_ids)

    leaderboard = {
        "event_id": event.id,
        "event_name": event.name,
        "is_team_event": True,
        "scoring_type": scoring_code,
        "entries": all_team_entries,
        "total_teams": len(team_scores),
        "total_catches": len(approved_catches) - total_dq_catches,
        "disqualified_members_count": total_dq_users,
        "disqualified_catches_excluded": total_dq_catches,
        "no_catch_teams_count": len(no_catch_teams),
        "last_updated": last_updated.isoformat() if last_updated else None,
    }

    return {
        "leaderboard": leaderboard,
        "user_details": {},
        "team_details": team_details,
    }


def _calculate_catch_details(
    catches: list[Catch],
    fish_scoring: dict[int, EventFishScoring],
    scoring_code: str,
    top_x_overall: int,
    is_team: bool = False,
) -> list[dict]:
    """Calculate detailed scoring info for each catch."""
    details = []

    if "top_x_overall" in scoring_code:
        for i, catch in enumerate(catches):
            is_scored = i < top_x_overall
            rank_in_category = i + 1

            fish_config = fish_scoring.get(catch.fish_id)
            min_length = fish_config.accountable_min_length if fish_config else 0
            under_min_pts = fish_config.under_min_length_points if fish_config else 0
            is_under_min = catch.length < min_length

            if is_scored:
                points = under_min_pts if is_under_min else int(catch.length)
            else:
                points = 0

            reason_not_scored = None
            if not is_scored:
                reason_not_scored = f"Exceeded slot limit ({rank_in_category} of {top_x_overall})"

            user_name = ""
            if catch.user and catch.user.profile:
                user_name = f"{catch.user.profile.first_name} {catch.user.profile.last_name}"

            detail = {
                "catch_id": catch.id,
                "fish_id": catch.fish_id,
                "fish_name": catch.fish.name if catch.fish else "Unknown",
                "length": catch.length,
                "points": points,
                "photo_url": catch.photo_url,
                "submitted_at": (catch.catch_time or catch.submitted_at).isoformat() if catch.catch_time or catch.submitted_at else None,
                "min_length": min_length,
                "is_under_min": is_under_min,
                "under_min_points": under_min_pts,
                "is_scored": is_scored,
                "rank_in_category": rank_in_category,
                "slot_limit": top_x_overall,
                "reason_not_scored": reason_not_scored,
                "uploaded_by_id": catch.user_id,
                "uploaded_by_name": user_name,
                "uploaded_by_initials": get_initials(user_name),
            }
            details.append(detail)
    else:
        catches_by_species: dict[int, list[Catch]] = defaultdict(list)
        for catch in catches:
            catches_by_species[catch.fish_id].append(catch)

        for fish_id, species_catches in catches_by_species.items():
            species_catches.sort(key=lambda c: c.length, reverse=True)

            fish_config = fish_scoring.get(fish_id)
            slots = fish_config.accountable_catch_slots if fish_config else 5
            min_length = fish_config.accountable_min_length if fish_config else 0
            under_min_pts = fish_config.under_min_length_points if fish_config else 0

            for i, catch in enumerate(species_catches):
                is_scored = i < slots
                rank_in_category = i + 1
                is_under_min = catch.length < min_length

                if is_scored:
                    points = under_min_pts if is_under_min else int(catch.length)
                else:
                    points = 0

                reason_not_scored = None
                if not is_scored:
                    reason_not_scored = f"Exceeded slot limit ({rank_in_category} of {slots})"

                user_name = ""
                if catch.user and catch.user.profile:
                    user_name = f"{catch.user.profile.first_name} {catch.user.profile.last_name}"

                detail = {
                    "catch_id": catch.id,
                    "fish_id": catch.fish_id,
                    "fish_name": catch.fish.name if catch.fish else "Unknown",
                    "length": catch.length,
                    "points": points,
                    "photo_url": catch.photo_url,
                    "submitted_at": (catch.catch_time or catch.submitted_at).isoformat() if catch.catch_time or catch.submitted_at else None,
                    "min_length": min_length,
                    "is_under_min": is_under_min,
                    "under_min_points": under_min_pts,
                    "is_scored": is_scored,
                    "rank_in_category": rank_in_category,
                    "slot_limit": slots,
                    "reason_not_scored": reason_not_scored,
                    "uploaded_by_id": catch.user_id,
                    "uploaded_by_name": user_name,
                    "uploaded_by_initials": get_initials(user_name),
                }
                details.append(detail)

    details.sort(key=lambda x: (-x["points"], not x["is_scored"]))
    return details


def _build_rejected_details(rejected_catches: list[Catch], is_team: bool = False) -> list[dict]:
    """Build rejected catch details list."""
    result = []
    for catch in rejected_catches:
        user_name = ""
        if catch.user and catch.user.profile:
            user_name = f"{catch.user.profile.first_name} {catch.user.profile.last_name}"

        rejected_by_name = ""
        if catch.validated_by and catch.validated_by.profile:
            rejected_by_name = f"{catch.validated_by.profile.first_name} {catch.validated_by.profile.last_name}"

        result.append({
            "catch_id": catch.id,
            "fish_id": catch.fish_id,
            "fish_name": catch.fish.name if catch.fish else "Unknown",
            "length": catch.length,
            "photo_url": catch.photo_url,
            "submitted_at": catch.submitted_at.isoformat() if catch.submitted_at else None,
            "rejected_at": catch.validated_at.isoformat() if catch.validated_at else None,
            "rejected_by": rejected_by_name,
            "rejection_reason": catch.rejection_reason or "No reason provided",
            "uploaded_by_id": catch.user_id,
            "uploaded_by_name": user_name,
            "uploaded_by_initials": get_initials(user_name),
        })
    return result


def _entries_are_tied(entry1: dict, entry2: dict) -> bool:
    """Check if two leaderboard entries are tied (all 6 criteria match)."""
    return (
        entry1["total_points"] == entry2["total_points"]
        and entry1["counted_catches"] == entry2["counted_catches"]
        and entry1["species_count"] == entry2["species_count"]
        and entry1["best_catch_length"] == entry2["best_catch_length"]
        and entry1["average_catch"] == entry2["average_catch"]
        and entry1.get("first_catch_time") == entry2.get("first_catch_time")
    )


# Convenience function to queue the task
def queue_leaderboard_recalculation(event_id: int, triggered_by: str = "unknown"):
    """Queue a leaderboard recalculation task."""
    recalculate_event_leaderboard.delay(event_id, triggered_by)
