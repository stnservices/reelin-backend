"""Leaderboard and scoring endpoints with proper scoring calculation."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user_optional
from app.models.user import UserAccount, UserProfile
from app.models.follow import UserFollow
from app.models.event import Event, EventFishScoring, EventSpeciesBonusPoints
from app.models.catch import Catch, CatchStatus, EventScoreboard, RankingMovement
from app.models.fish import Fish
from app.models.team import Team, TeamMember
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.services.redis_cache import redis_cache
from app.tasks.leaderboard import queue_leaderboard_recalculation

router = APIRouter()


def _entries_are_tied(entry1: dict, entry2: dict) -> bool:
    """Check if two leaderboard entries are tied (all 6 criteria match)."""
    return (
        entry1["total_points"] == entry2["total_points"]
        and entry1["counted_catches"] == entry2["counted_catches"]
        and entry1["species_count"] == entry2["species_count"]
        and (entry1.get("best_catch_length") or 0) == (entry2.get("best_catch_length") or 0)
        and entry1.get("average_catch", 0) == entry2.get("average_catch", 0)
        and entry1.get("first_catch_time") == entry2.get("first_catch_time")
    )


class ScoringCalculator:
    """
    Calculates scores based on event's scoring config code.

    Scoring Types:
    1. top_x_by_species: Top N catches per species (slot-based)
    2. top_x_overall: Top N catches globally regardless of species

    All types can include species diversity bonus points.
    """

    def __init__(
        self,
        scoring_code: str,
        top_x_overall: Optional[int],
        fish_scoring: list[EventFishScoring],
        bonus_points: list[EventSpeciesBonusPoints],
    ):
        self.scoring_code = scoring_code
        self.top_x_overall = top_x_overall or 10
        self.fish_scoring = {fs.fish_id: fs for fs in fish_scoring}
        self.bonus_points = sorted(bonus_points, key=lambda x: x.species_count, reverse=True)

    def calculate_score(self, catches: list[Catch]) -> dict:
        """
        Calculate total score for a user's catches.

        Returns:
            dict with:
            - total_points: Total score
            - counted_catches: Number of catches counted
            - species_bonus: Bonus points from species diversity
            - breakdown: Per-species breakdown
        """
        if not catches:
            return {
                "total_points": 0,
                "counted_catches": 0,
                "species_bonus": 0,
                "breakdown": {},
            }

        # Group catches by fish species
        by_species: dict[int, list[Catch]] = {}
        for catch in catches:
            if catch.fish_id not in by_species:
                by_species[catch.fish_id] = []
            by_species[catch.fish_id].append(catch)

        # Sort each species' catches by length (descending)
        for fish_id in by_species:
            by_species[fish_id] = sorted(by_species[fish_id], key=lambda c: c.length, reverse=True)

        total_points = 0
        counted_catches = 0
        breakdown = {}

        # Check for top_x_overall scoring (handles both 'top_x_overall' and 'sf_top_x_overall')
        if "top_x_overall" in self.scoring_code:
            # Top X catches globally regardless of species
            all_catches = sorted(catches, key=lambda c: c.length, reverse=True)
            for catch in all_catches[:self.top_x_overall]:
                # Apply under-min scoring rules
                fish_config = self.fish_scoring.get(catch.fish_id)
                min_length = fish_config.accountable_min_length if fish_config else 0
                under_min_pts = fish_config.under_min_length_points if fish_config else 0

                if catch.length >= min_length:
                    points = catch.length
                else:
                    points = under_min_pts

                total_points += points
                counted_catches += 1
                fish_id = catch.fish_id
                if fish_id not in breakdown:
                    breakdown[fish_id] = {"catches_counted": 0, "points": 0}
                breakdown[fish_id]["catches_counted"] += 1
                breakdown[fish_id]["points"] += points

        else:
            # Default: top_x_by_species - Top N catches per species (slot-based)
            # Handles 'top_x_by_species', 'sf_top_x_by_species', etc.
            for fish_id, species_catches in by_species.items():
                fish_config = self.fish_scoring.get(fish_id)
                slots = fish_config.accountable_catch_slots if fish_config else 5
                min_length = fish_config.accountable_min_length if fish_config else 0

                counted = 0
                species_points = 0
                for catch in species_catches[:slots]:
                    if catch.length >= min_length:
                        species_points += catch.length
                    elif fish_config:
                        species_points += fish_config.under_min_length_points
                    counted += 1

                breakdown[fish_id] = {
                    "catches_counted": counted,
                    "slots": slots,
                    "points": species_points,
                }
                total_points += species_points
                counted_catches += counted

        # Calculate species diversity bonus
        species_bonus = 0
        num_species = len(by_species)
        for bp in self.bonus_points:
            if num_species >= bp.species_count:
                species_bonus = bp.bonus_points
                break

        total_points += species_bonus

        return {
            "total_points": total_points,
            "counted_catches": counted_catches,
            "species_bonus": species_bonus,
            "breakdown": breakdown,
            "num_species": num_species,
        }


async def _calculate_catch_details_for_fallback(
    catches: list[Catch],
    event: Event,
    db: AsyncSession,
) -> tuple[list[dict], int, int]:
    """
    Calculate catch details with proper is_scored flags for API fallback.

    This is used when Redis cache is empty. It properly calculates which
    catches are scored based on slot limits, instead of marking all as scored.

    Returns:
        tuple of (catch_details_list, scored_count, not_scored_count)
    """
    from collections import defaultdict

    if not catches:
        return [], 0, 0

    # Load fish scoring config for this event
    fish_scoring_query = select(EventFishScoring).where(
        EventFishScoring.event_id == event.id
    )
    fish_scoring_result = await db.execute(fish_scoring_query)
    fish_scoring_list = fish_scoring_result.scalars().all()
    fish_scoring = {fs.fish_id: fs for fs in fish_scoring_list}

    # Get scoring config
    scoring_code = event.scoring_config.code if event.scoring_config else "top_x_by_species"
    top_x_overall = event.top_x_overall or (
        event.scoring_config.default_top_x if event.scoring_config else 10
    )

    details = []

    if "top_x_overall" in scoring_code:
        # Global top X scoring - first N catches count
        sorted_catches = sorted(catches, key=lambda c: c.length, reverse=True)
        for i, catch in enumerate(sorted_catches):
            is_scored = i < top_x_overall
            fish_config = fish_scoring.get(catch.fish_id)
            min_length = fish_config.accountable_min_length if fish_config else 0
            under_min_pts = fish_config.under_min_length_points if fish_config else 0
            is_under_min = catch.length < min_length

            # Calculate points with under-min scoring
            if is_scored:
                points = under_min_pts if is_under_min else catch.length
            else:
                points = 0

            details.append({
                "catch_id": catch.id,
                "fish_id": catch.fish_id,
                "fish_name": catch.fish.name if catch.fish else "Unknown",
                "length": catch.length,
                "points": points,
                "photo_url": catch.photo_url,
                "submitted_at": catch.submitted_at.isoformat() if catch.submitted_at else None,
                "is_scored": is_scored,
                "is_under_min": is_under_min,
                "under_min_points": under_min_pts,
                "min_length": min_length,
                "rank_in_category": i + 1,
                "slot_limit": top_x_overall,
                "reason_not_scored": f"Exceeded slot limit ({i + 1} of {top_x_overall})" if not is_scored else None,
            })
    else:
        # Top X by species - slot limits per species
        catches_by_species: dict[int, list[Catch]] = defaultdict(list)
        for catch in catches:
            catches_by_species[catch.fish_id].append(catch)

        for fish_id, species_catches in catches_by_species.items():
            # Sort by length within species
            species_catches.sort(key=lambda c: c.length, reverse=True)

            fish_config = fish_scoring.get(fish_id)
            slots = fish_config.accountable_catch_slots if fish_config else 5
            min_length = fish_config.accountable_min_length if fish_config else 0
            under_min_pts = fish_config.under_min_length_points if fish_config else 0

            for i, catch in enumerate(species_catches):
                is_scored = i < slots
                is_under_min = catch.length < min_length

                # Calculate points with under-min scoring
                if is_scored:
                    points = under_min_pts if is_under_min else catch.length
                else:
                    points = 0

                details.append({
                    "catch_id": catch.id,
                    "fish_id": catch.fish_id,
                    "fish_name": catch.fish.name if catch.fish else "Unknown",
                    "length": catch.length,
                    "points": points,
                    "photo_url": catch.photo_url,
                    "submitted_at": catch.submitted_at.isoformat() if catch.submitted_at else None,
                    "is_scored": is_scored,
                    "is_under_min": is_under_min,
                    "under_min_points": under_min_pts,
                    "min_length": min_length,
                    "rank_in_category": i + 1,
                    "slot_limit": slots,
                    "reason_not_scored": f"Exceeded slot limit ({i + 1} of {slots})" if not is_scored else None,
                })

    # Sort: scored first (by points desc), then not scored
    details.sort(key=lambda x: (-x["points"], not x["is_scored"]))

    scored_count = sum(1 for d in details if d["is_scored"])
    not_scored_count = len(details) - scored_count

    return details, scored_count, not_scored_count


@router.get("/events/{event_id}")
async def get_event_leaderboard(
    event_id: int,
    limit: int = Query(50, ge=1, le=200),
    include_breakdown: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[UserAccount] = Depends(get_current_user_optional),
):
    """
    Get real-time leaderboard for an event.

    This is a PUBLIC endpoint - authentication is optional.
    If authenticated, includes `is_following` field for each entry.

    For TEAM events: Returns team-based rankings with aggregated catches.
    For INDIVIDUAL events: Returns user-based rankings.

    The leaderboard is calculated based on the event's scoring configuration:
    - top_n_by_species: Sum of top N catches per species (default)
    - top_n_overall: Sum of top N catches globally

    Plus species diversity bonus points if configured.

    Leaderboard is always calculated fresh from DB for reliability.
    """
    # Get list of users the current user follows (if authenticated)
    following_user_ids: set[int] = set()
    if current_user:
        following_result = await db.execute(
            select(UserFollow.following_id).where(
                UserFollow.follower_id == current_user.id
            )
        )
        following_user_ids = {row[0] for row in following_result.fetchall()}

    # Load event with scoring config
    event_query = (
        select(Event)
        .options(
            selectinload(Event.scoring_config),
        )
        .where(Event.id == event_id)
    )
    result = await db.execute(event_query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Load fish scoring config for this event
    fish_scoring_query = (
        select(EventFishScoring)
        .options(selectinload(EventFishScoring.fish))
        .where(EventFishScoring.event_id == event_id)
    )
    fish_scoring_result = await db.execute(fish_scoring_query)
    fish_scoring = fish_scoring_result.scalars().all()

    # Load bonus points config
    bonus_query = (
        select(EventSpeciesBonusPoints)
        .where(EventSpeciesBonusPoints.event_id == event_id)
        .order_by(EventSpeciesBonusPoints.species_count.desc())
    )
    bonus_result = await db.execute(bonus_query)
    bonus_points = bonus_result.scalars().all()

    # Create calculator using scoring config code
    calculator = ScoringCalculator(
        scoring_code=event.scoring_config.code if event.scoring_config else "top_x_by_species",
        top_x_overall=event.top_x_overall,
        fish_scoring=list(fish_scoring),
        bonus_points=list(bonus_points),
    )

    # Get all approved catches for this event
    catches_query = (
        select(Catch)
        .options(
            selectinload(Catch.user).selectinload(UserAccount.profile),
            selectinload(Catch.fish),
        )
        .where(
            Catch.event_id == event_id,
            Catch.status == CatchStatus.APPROVED.value,
        )
        .order_by(Catch.user_id, Catch.length.desc())
    )
    catches_result = await db.execute(catches_query)
    all_catches = catches_result.scalars().all()

    # Check if this is a team event
    if event.is_team_event:
        result = await _get_team_leaderboard(
            db, event, calculator, all_catches, limit, include_breakdown, following_user_ids
        )
    else:
        result = await _get_individual_leaderboard(
            db, event, calculator, all_catches, limit, include_breakdown, following_user_ids
        )

    # Add viewer count from Redis (still useful for live view)
    try:
        result["viewer_count"] = await redis_cache.get_viewer_count(event_id)
    except Exception:
        result["viewer_count"] = 0

    return result


async def _get_individual_leaderboard(
    db: AsyncSession,
    event: Event,
    calculator: "ScoringCalculator",
    all_catches: list[Catch],
    limit: int,
    include_breakdown: bool,
    following_user_ids: set[int],
) -> dict:
    """Calculate individual-based leaderboard."""
    # Get disqualified users for this event
    disqualified_query = select(EventEnrollment).where(
        EventEnrollment.event_id == event.id,
        EventEnrollment.status == EnrollmentStatus.DISQUALIFIED.value,
    )
    disqualified_result = await db.execute(disqualified_query)
    disqualified_enrollments = disqualified_result.scalars().all()
    disqualified_user_ids = {e.user_id for e in disqualified_enrollments}
    disqualified_info = {
        e.user_id: {
            "reason": e.disqualification_reason,
            "at": e.disqualified_at,
        }
        for e in disqualified_enrollments
    }

    # Get penalty points from EventScoreboard for all users in this event
    scoreboard_query = select(EventScoreboard).where(
        EventScoreboard.event_id == event.id,
    )
    scoreboard_result = await db.execute(scoreboard_query)
    scoreboards = scoreboard_result.scalars().all()
    user_penalty_points = {sb.user_id: sb.penalty_points for sb in scoreboards}

    # Get fish scoring configs to determine accountable min lengths
    fish_scoring_query = select(EventFishScoring).where(
        EventFishScoring.event_id == event.id
    )
    fish_scoring_result = await db.execute(fish_scoring_query)
    fish_scoring_configs = {fs.fish_id: fs.accountable_min_length for fs in fish_scoring_result.scalars().all()}

    # Group catches by user (excluding disqualified from rankings)
    user_catches: dict[int, list[Catch]] = {}
    disqualified_catches: dict[int, list[Catch]] = {}

    for catch in all_catches:
        if catch.user_id in disqualified_user_ids:
            # Track disqualified users' catches separately
            if catch.user_id not in disqualified_catches:
                disqualified_catches[catch.user_id] = []
            disqualified_catches[catch.user_id].append(catch)
        else:
            if catch.user_id not in user_catches:
                user_catches[catch.user_id] = []
            user_catches[catch.user_id].append(catch)

    # Calculate scores for each user
    user_scores = []
    for user_id, catches in user_catches.items():
        score_data = calculator.calculate_score(catches)
        user = catches[0].user  # All catches have same user

        # Find best ACCOUNTABLE catch (length >= min_length for that species)
        accountable_catches = [
            c for c in catches
            if c.length >= fish_scoring_configs.get(c.fish_id, 0)
        ]
        best_catch = max(accountable_catches, key=lambda c: c.length) if accountable_catches else None

        # Calculate average catch length (for tiebreaker)
        total_length = sum(c.length for c in catches)
        average_catch = round(total_length / len(catches), 2) if catches else 0.0

        # First catch time (earliest wins in tiebreaker)
        first_catch_time = min(
            (c.validated_at or c.submitted_at for c in catches),
            default=None
        )

        # Last upload time (most recent catch upload)
        last_upload_time = max(
            (c.submitted_at for c in catches if c.submitted_at),
            default=None
        )

        # Get penalty points for this user (from contestations)
        penalty_points = user_penalty_points.get(user_id, 0)
        # Deduct penalty from total
        total_points_with_penalty = max(0, score_data["total_points"] - penalty_points)

        entry = {
            "user_id": user_id,
            "user_name": f"{user.profile.first_name} {user.profile.last_name}" if user.profile else f"User {user_id}",
            "avatar_url": user.profile.profile_picture_url if user.profile else None,
            "total_points": total_points_with_penalty,
            "total_catches": len(catches),
            "counted_catches": score_data["counted_catches"],
            "species_count": score_data["num_species"],
            "species_bonus": score_data["species_bonus"],
            "penalty_points": penalty_points,
            "best_catch_id": best_catch.id if best_catch else None,
            "best_catch_length": best_catch.length if best_catch else None,
            "best_catch_species": best_catch.fish.name if best_catch else None,
            "best_catch_photo_url": best_catch.photo_url if best_catch else None,
            "average_catch": average_catch,
            "first_catch_time": first_catch_time,
            "last_upload_time": last_upload_time,
            "is_following": user_id in following_user_ids,
        }

        if include_breakdown:
            entry["breakdown"] = score_data["breakdown"]

        user_scores.append(entry)

    # Sort with 6-level tiebreaker chain (matching old system)
    # 1. Total points (highest), 2. Counted catches (highest), 3. Species count (highest)
    # 4. Best catch (highest), 5. Average catch (highest), 6. First catch time (earliest)
    user_scores.sort(key=lambda x: (
        -x["total_points"],
        -x["counted_catches"],
        -x["species_count"],
        -(x["best_catch_length"] or 0),
        -x["average_catch"],
        x["first_catch_time"] or datetime.max,
    ))

    # Assign ranks with proper tie handling (same criteria = same rank)
    current_rank = 1
    prev_entry = None
    for i, entry in enumerate(user_scores[:limit], start=1):
        if prev_entry and _entries_are_tied(entry, prev_entry):
            entry["rank"] = current_rank  # Same rank as previous
        else:
            current_rank = i
            entry["rank"] = current_rank
        entry["is_disqualified"] = False
        prev_entry = entry

    # Build disqualified entries (shown at bottom, unranked)
    disqualified_entries = []
    for user_id, catches in disqualified_catches.items():
        score_data = calculator.calculate_score(catches)
        user = catches[0].user

        # Find best ACCOUNTABLE catch
        accountable_catches_dq = [
            c for c in catches
            if c.length >= fish_scoring_configs.get(c.fish_id, 0)
        ]
        best_catch = max(accountable_catches_dq, key=lambda c: c.length) if accountable_catches_dq else None
        total_length = sum(c.length for c in catches)
        average_catch = round(total_length / len(catches), 2) if catches else 0.0

        dq_info = disqualified_info.get(user_id, {})

        # Get penalty points for disqualified user
        penalty_points = user_penalty_points.get(user_id, 0)

        entry = {
            "user_id": user_id,
            "user_name": f"{user.profile.first_name} {user.profile.last_name}" if user.profile else f"User {user_id}",
            "avatar_url": user.profile.profile_picture_url if user.profile else None,
            "total_points": score_data["total_points"],
            "total_catches": len(catches),
            "counted_catches": 0,  # Not counted due to disqualification
            "species_count": score_data["num_species"],
            "species_bonus": 0,
            "penalty_points": penalty_points,
            "best_catch_id": best_catch.id if best_catch else None,
            "best_catch_length": best_catch.length if best_catch else None,
            "best_catch_species": best_catch.fish.name if best_catch else None,
            "best_catch_photo_url": best_catch.photo_url if best_catch else None,
            "average_catch": average_catch,
            "rank": None,  # No rank for disqualified
            "is_disqualified": True,
            "disqualification_reason": dq_info.get("reason"),
            "disqualified_at": dq_info.get("at").isoformat() if dq_info.get("at") else None,
            "is_following": user_id in following_user_ids,
        }
        disqualified_entries.append(entry)

    # Get last catch time for "last updated"
    last_catch_time = None
    if all_catches:
        last_catch_time = max(c.validated_at or c.submitted_at for c in all_catches)

    # Combine ranked entries + disqualified at bottom
    all_entries = user_scores[:limit] + disqualified_entries

    # For completed events, include enrolled users with 0 catches
    no_catch_entries = []
    no_catch_count = 0
    if event.status == "completed":
        # Get all users who have activity (catches or disqualified)
        users_with_activity = set(user_catches.keys()) | set(disqualified_catches.keys())

        # Query enrolled users with APPROVED status who have no catches
        enrolled_query = (
            select(EventEnrollment)
            .options(selectinload(EventEnrollment.user).selectinload(UserAccount.profile))
            .where(
                EventEnrollment.event_id == event.id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
            )
        )
        if users_with_activity:
            enrolled_query = enrolled_query.where(
                EventEnrollment.user_id.notin_(users_with_activity)
            )

        enrolled_result = await db.execute(enrolled_query)
        enrolled_no_catches = enrolled_result.scalars().all()

        if enrolled_no_catches:
            # All 0-catch participants share the same rank = last_rank + 1
            # Use len of limited list since ranks are only assigned to user_scores[:limit]
            ranked_count = min(len(user_scores), limit)
            last_rank = user_scores[ranked_count - 1]["rank"] if ranked_count > 0 else 0
            shared_rank = last_rank + 1

            for enrollment in enrolled_no_catches:
                user = enrollment.user
                if not user:
                    continue

                entry = {
                    "user_id": user.id,
                    "user_name": f"{user.profile.first_name} {user.profile.last_name}" if user.profile else f"User {user.id}",
                    "avatar_url": user.profile.profile_picture_url if user.profile else None,
                    "total_points": 0,
                    "total_catches": 0,
                    "counted_catches": 0,
                    "species_count": 0,
                    "species_bonus": 0,
                    "penalty_points": 0,
                    "best_catch_id": None,
                    "best_catch_length": None,
                    "best_catch_species": None,
                    "best_catch_photo_url": None,
                    "average_catch": 0,
                    "first_catch_time": None,
                    "last_upload_time": None,
                    "rank": shared_rank,
                    "is_disqualified": False,
                    "has_no_catches": True,
                    "is_following": user.id in following_user_ids,
                }
                no_catch_entries.append(entry)

            no_catch_count = len(no_catch_entries)
            all_entries = all_entries + no_catch_entries

    return {
        "event_id": event.id,
        "event_name": event.name,
        "is_team_event": False,
        "scoring_type": event.scoring_config.code if event.scoring_config else "top_x_by_species",
        "entries": all_entries,
        "total_participants": len(user_scores),  # Only count ranked participants
        "total_catches": len(all_catches) - sum(len(c) for c in disqualified_catches.values()),  # Exclude DQ catches
        "disqualified_count": len(disqualified_entries),
        "no_catch_participants_count": no_catch_count,
        "last_updated": last_catch_time.isoformat() if last_catch_time else None,
    }


async def _get_team_leaderboard(
    db: AsyncSession,
    event: Event,
    calculator: "ScoringCalculator",
    all_catches: list[Catch],
    limit: int,
    include_breakdown: bool,
    following_user_ids: set[int],
) -> dict:
    """Calculate team-based leaderboard with aggregated catches from all team members."""

    # Get disqualified users for this event
    disqualified_query = select(EventEnrollment).where(
        EventEnrollment.event_id == event.id,
        EventEnrollment.status == EnrollmentStatus.DISQUALIFIED.value,
    )
    disqualified_result = await db.execute(disqualified_query)
    disqualified_enrollments = disqualified_result.scalars().all()
    disqualified_user_ids = {e.user_id for e in disqualified_enrollments}
    disqualified_info = {
        e.user_id: {
            "reason": e.disqualification_reason,
            "at": e.disqualified_at,
        }
        for e in disqualified_enrollments
    }

    # Get penalty points from EventScoreboard for all users in this event
    scoreboard_query = select(EventScoreboard).where(
        EventScoreboard.event_id == event.id,
    )
    scoreboard_result = await db.execute(scoreboard_query)
    scoreboards = scoreboard_result.scalars().all()
    user_penalty_points = {sb.user_id: sb.penalty_points for sb in scoreboards}

    # Get fish scoring configs to determine accountable min lengths
    fish_scoring_query = select(EventFishScoring).where(
        EventFishScoring.event_id == event.id
    )
    fish_scoring_result = await db.execute(fish_scoring_query)
    fish_scoring_configs = {fs.fish_id: fs.accountable_min_length for fs in fish_scoring_result.scalars().all()}

    # Get all teams for this event with their members (including user profiles)
    teams_query = (
        select(Team)
        .options(
            selectinload(Team.members)
            .selectinload(TeamMember.enrollment)
            .selectinload(EventEnrollment.user)
            .selectinload(UserAccount.profile),
        )
        .where(Team.event_id == event.id, Team.is_active == True)
    )
    teams_result = await db.execute(teams_query)
    teams = teams_result.scalars().all()

    # Build mapping: user_id -> team_id
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
                # Track if member is disqualified
                is_dq = user_id in disqualified_user_ids
                dq_info = disqualified_info.get(user_id, {})
                # Get user name from profile
                user = member.enrollment.user
                user_name = f"{user.profile.first_name} {user.profile.last_name}" if user and user.profile else f"User {user_id}"
                team_info[team.id]["members"].append({
                    "user_id": user_id,
                    "user_name": user_name,
                    "role": member.role,
                    "catches_count": 0,
                    "is_disqualified": is_dq,
                    "disqualification_reason": dq_info.get("reason") if is_dq else None,
                    "is_following": user_id in following_user_ids,
                })

    # Group catches by team (excluding catches from disqualified members)
    team_catches: dict[int, list[Catch]] = {}
    user_catch_counts: dict[int, int] = {}
    disqualified_catch_count: dict[int, int] = {}  # Track DQ catches per team

    for catch in all_catches:
        team_id = user_to_team.get(catch.user_id)
        if team_id:
            # Track per-user catch count for team member info (even for DQ members)
            user_catch_counts[catch.user_id] = user_catch_counts.get(catch.user_id, 0) + 1

            # Exclude catches from disqualified members from team scoring
            if catch.user_id in disqualified_user_ids:
                disqualified_catch_count[team_id] = disqualified_catch_count.get(team_id, 0) + 1
                continue  # Don't add to team catches for scoring

            if team_id not in team_catches:
                team_catches[team_id] = []
            team_catches[team_id].append(catch)

    # Calculate scores for each team
    team_scores = []
    for team_id, catches in team_catches.items():
        score_data = calculator.calculate_score(catches)

        # Find best ACCOUNTABLE catch (length >= min_length for that species)
        accountable_catches_team = [
            c for c in catches
            if c.length >= fish_scoring_configs.get(c.fish_id, 0)
        ]
        best_catch = max(accountable_catches_team, key=lambda c: c.length) if accountable_catches_team else None

        # Update member catch counts
        members_with_counts = []
        for member in team_info[team_id]["members"]:
            member_copy = member.copy()
            member_copy["catches_count"] = user_catch_counts.get(member["user_id"], 0)
            members_with_counts.append(member_copy)

        # Calculate average catch length (for tiebreaker)
        total_length = sum(c.length for c in catches)
        average_catch = round(total_length / len(catches), 2) if catches else 0.0

        # First catch time (earliest wins in tiebreaker)
        first_catch_time = min(
            (c.validated_at or c.submitted_at for c in catches),
            default=None
        )

        # Last upload time (most recent catch upload)
        last_upload_time = max(
            (c.submitted_at for c in catches if c.submitted_at),
            default=None
        )

        # Get best catch user name
        best_catch_user_name = None
        if best_catch and best_catch.user:
            if best_catch.user.profile:
                best_catch_user_name = f"{best_catch.user.profile.first_name} {best_catch.user.profile.last_name}"
            else:
                best_catch_user_name = f"User {best_catch.user_id}"

        # Count disqualified members in this team
        dq_members = [m for m in members_with_counts if m.get("is_disqualified")]
        dq_catches_excluded = disqualified_catch_count.get(team_id, 0)

        # Calculate team penalty points (sum of all non-disqualified members' penalties)
        team_penalty_points = sum(
            user_penalty_points.get(m["user_id"], 0)
            for m in members_with_counts
            if not m.get("is_disqualified")
        )
        # Deduct penalty from total
        total_points_with_penalty = max(0, score_data["total_points"] - team_penalty_points)

        # Check if any team member is followed by the current user
        has_followed_member = any(
            m.get("is_following") for m in members_with_counts
        )

        entry = {
            "team_id": team_id,
            "team_name": team_info[team_id]["name"],
            "team_logo_url": team_info[team_id]["logo_url"],
            "total_points": total_points_with_penalty,
            "total_catches": len(catches),
            "counted_catches": score_data["counted_catches"],
            "species_count": score_data["num_species"],
            "species_bonus": score_data["species_bonus"],
            "penalty_points": team_penalty_points,
            "best_catch_id": best_catch.id if best_catch else None,
            "best_catch_length": best_catch.length if best_catch else None,
            "best_catch_species": best_catch.fish.name if best_catch else None,
            "best_catch_photo_url": best_catch.photo_url if best_catch else None,
            "best_catch_user_id": best_catch.user_id if best_catch else None,
            "best_catch_user_name": best_catch_user_name,
            "average_catch": average_catch,
            "first_catch_time": first_catch_time,
            "last_upload_time": last_upload_time,
            "members": members_with_counts,
            "member_count": len(members_with_counts),
            "disqualified_members_count": len(dq_members),
            "disqualified_catches_excluded": dq_catches_excluded,
            "has_followed_member": has_followed_member,
        }

        if include_breakdown:
            entry["breakdown"] = score_data["breakdown"]

        team_scores.append(entry)

    # Also include teams with no catches (or only disqualified catches)
    for team_id, info in team_info.items():
        if team_id not in team_catches:
            # Update member catch counts for teams with no scoring catches
            members_with_counts = []
            for member in info["members"]:
                member_copy = member.copy()
                member_copy["catches_count"] = user_catch_counts.get(member["user_id"], 0)
                members_with_counts.append(member_copy)

            dq_members = [m for m in members_with_counts if m.get("is_disqualified")]
            dq_catches_excluded = disqualified_catch_count.get(team_id, 0)

            # Calculate team penalty points (sum of all non-disqualified members' penalties)
            team_penalty_points = sum(
                user_penalty_points.get(m["user_id"], 0)
                for m in members_with_counts
                if not m.get("is_disqualified")
            )

            # Check if any team member is followed by the current user
            has_followed_member = any(
                m.get("is_following") for m in members_with_counts
            )

            team_scores.append({
                "team_id": team_id,
                "team_name": info["name"],
                "team_logo_url": info["logo_url"],
                "total_points": 0,
                "total_catches": 0,
                "counted_catches": 0,
                "species_count": 0,
                "species_bonus": 0,
                "penalty_points": team_penalty_points,
                "best_catch_id": None,
                "best_catch_length": None,
                "best_catch_species": None,
                "best_catch_photo_url": None,
                "best_catch_user_id": None,
                "best_catch_user_name": None,
                "average_catch": 0.0,
                "first_catch_time": None,
                "last_upload_time": None,
                "members": members_with_counts,
                "member_count": len(members_with_counts),
                "disqualified_members_count": len(dq_members),
                "disqualified_catches_excluded": dq_catches_excluded,
                "has_followed_member": has_followed_member,
            })

    # Sort with 6-level tiebreaker chain (matching old system)
    # 1. Total points (highest), 2. Counted catches (highest), 3. Species count (highest)
    # 4. Best catch (highest), 5. Average catch (highest), 6. First catch time (earliest)
    team_scores.sort(key=lambda x: (
        -x["total_points"],
        -x["counted_catches"],
        -x["species_count"],
        -(x["best_catch_length"] or 0),
        -x["average_catch"],
        x["first_catch_time"] or datetime.max,
    ))

    # Assign ranks with proper tie handling (same criteria = same rank)
    current_rank = 1
    prev_entry = None
    for i, entry in enumerate(team_scores[:limit], start=1):
        if prev_entry and _entries_are_tied(entry, prev_entry):
            entry["rank"] = current_rank  # Same rank as previous
        else:
            current_rank = i
            entry["rank"] = current_rank
        prev_entry = entry

    # Get last catch time for "last updated"
    last_catch_time = None
    if all_catches:
        last_catch_time = max(c.validated_at or c.submitted_at for c in all_catches)

    # Calculate totals excluding disqualified catches
    total_dq_catches = sum(disqualified_catch_count.values())
    total_dq_users = len(disqualified_user_ids)

    return {
        "event_id": event.id,
        "event_name": event.name,
        "is_team_event": True,
        "scoring_type": event.scoring_config.code if event.scoring_config else "top_x_by_species",
        "entries": team_scores[:limit],
        "total_teams": len(team_scores),
        "total_catches": len(all_catches) - total_dq_catches,  # Exclude DQ catches from total
        "disqualified_members_count": total_dq_users,
        "disqualified_catches_excluded": total_dq_catches,
        "last_updated": last_catch_time.isoformat() if last_catch_time else None,
    }


@router.get("/events/{event_id}/user/{user_id}")
async def get_user_standing(
    event_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed standing for a specific user in an event.
    Shows their rank, catches, and scoring breakdown.
    """
    # First get the full leaderboard to determine rank
    leaderboard = await get_event_leaderboard(event_id, limit=200, include_breakdown=True, db=db)

    # Find user in leaderboard
    user_entry = None
    for entry in leaderboard["entries"]:
        if entry["user_id"] == user_id:
            user_entry = entry
            break

    if not user_entry:
        # User has no approved catches
        return {
            "event_id": event_id,
            "user_id": user_id,
            "rank": None,
            "total_points": 0,
            "total_catches": 0,
            "message": "No approved catches yet",
        }

    # Get user's catches with details
    catches_query = (
        select(Catch)
        .options(selectinload(Catch.fish))
        .where(
            Catch.event_id == event_id,
            Catch.user_id == user_id,
            Catch.status == CatchStatus.APPROVED.value,
        )
        .order_by(Catch.length.desc())
    )
    catches_result = await db.execute(catches_query)
    catches = catches_result.scalars().all()

    return {
        "event_id": event_id,
        "user_id": user_id,
        **user_entry,
        "catches": [
            {
                "id": c.id,
                "fish_id": c.fish_id,
                "fish_name": c.fish.name,
                "length": c.length,
                "weight": c.weight,
                "photo_url": c.photo_url,
                "catch_time": c.catch_time.isoformat() if c.catch_time else None,
                "validated_at": c.validated_at.isoformat() if c.validated_at else None,
            }
            for c in catches
        ],
    }


@router.get("/events/{event_id}/recent")
async def get_recent_catches(
    event_id: int,
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """
    Get recently validated catches for live feed.
    This is a PUBLIC endpoint.
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    if not event_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Event not found")

    # Get recent approved catches
    catches_query = (
        select(Catch)
        .options(
            selectinload(Catch.user).selectinload(UserAccount.profile),
            selectinload(Catch.fish),
        )
        .where(
            Catch.event_id == event_id,
            Catch.status == CatchStatus.APPROVED.value,
        )
        .order_by(Catch.validated_at.desc())
        .limit(limit)
    )
    catches_result = await db.execute(catches_query)
    catches = catches_result.scalars().all()

    return {
        "event_id": event_id,
        "catches": [
            {
                "id": c.id,
                "user_id": c.user_id,
                "user_name": f"{c.user.profile.first_name} {c.user.profile.last_name}" if c.user.profile else f"User {c.user_id}",
                "fish_id": c.fish_id,
                "fish_name": c.fish.name,
                "length": c.length,
                "weight": c.weight,
                "photo_url": c.photo_url,
                "thumbnail_url": c.thumbnail_url,
                "validated_at": c.validated_at.isoformat() if c.validated_at else None,
            }
            for c in catches
        ],
    }


@router.get("/events/{event_id}/movements")
async def get_ranking_movements(
    event_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    Get recent ranking movements for live scoreboard animations.
    Shows who moved up/down in rankings.

    For TEAM events: Returns team ranking movements.
    For INDIVIDUAL events: Returns user ranking movements.
    """
    # Check event exists and get event type
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get recent movements with appropriate relationships
    movements_query = (
        select(RankingMovement)
        .options(
            selectinload(RankingMovement.user).selectinload(UserAccount.profile),
            selectinload(RankingMovement.team),
            selectinload(RankingMovement.catch).selectinload(Catch.fish),
        )
        .where(RankingMovement.event_id == event_id)
        .order_by(RankingMovement.created_at.desc())
        .limit(limit)
    )
    movements_result = await db.execute(movements_query)
    movements = movements_result.scalars().all()

    if event.is_team_event:
        # Build user_id to team mapping for historical movements without team_id
        user_to_team: dict[int, tuple[int, str, str | None]] = {}
        teams_query = (
            select(Team)
            .options(selectinload(Team.members).selectinload(TeamMember.enrollment))
            .where(Team.event_id == event_id, Team.is_active == True)
        )
        teams_result = await db.execute(teams_query)
        teams = teams_result.scalars().all()
        for team in teams:
            for member in team.members:
                if member.is_active and member.enrollment:
                    user_to_team[member.enrollment.user_id] = (team.id, team.name, team.logo_url)

        # Team event: return team movements (including historical user movements mapped to teams)
        team_movements = []
        for m in movements:
            if m.team_id is not None:
                # Movement has team_id set
                team_movements.append({
                    "id": m.id,
                    "team_id": m.team_id,
                    "team_name": m.team.name if m.team else f"Team {m.team_id}",
                    "team_logo_url": m.team.logo_url if m.team else None,
                    "old_rank": m.old_rank,
                    "new_rank": m.new_rank,
                    "movement": m.movement,
                    "movement_emoji": m.movement_emoji,
                    "catch_fish": m.catch.fish.name if m.catch and m.catch.fish else None,
                    "catch_length": m.catch.length if m.catch else None,
                    "catch_user_id": m.catch.user_id if m.catch else None,
                    "created_at": m.created_at.isoformat(),
                })
            elif m.user_id and m.user_id in user_to_team:
                # Historical movement - map user to team
                team_id, team_name, team_logo = user_to_team[m.user_id]
                team_movements.append({
                    "id": m.id,
                    "team_id": team_id,
                    "team_name": team_name,
                    "team_logo_url": team_logo,
                    "old_rank": m.old_rank,
                    "new_rank": m.new_rank,
                    "movement": m.movement,
                    "movement_emoji": m.movement_emoji,
                    "catch_fish": m.catch.fish.name if m.catch and m.catch.fish else None,
                    "catch_length": m.catch.length if m.catch else None,
                    "catch_user_id": m.user_id,
                    "created_at": m.created_at.isoformat(),
                })

        return {
            "event_id": event_id,
            "is_team_event": True,
            "movements": team_movements,
        }
    else:
        # Individual event: return user movements
        return {
            "event_id": event_id,
            "is_team_event": False,
            "movements": [
                {
                    "id": m.id,
                    "user_id": m.user_id,
                    "user_name": f"{m.user.profile.first_name} {m.user.profile.last_name}" if m.user and m.user.profile else f"User {m.user_id}",
                    "avatar_url": m.user.profile.profile_picture_url if m.user and m.user.profile else None,
                    "old_rank": m.old_rank,
                    "new_rank": m.new_rank,
                    "movement": m.movement,  # Positive = moved up
                    "movement_emoji": m.movement_emoji,
                    "catch_fish": m.catch.fish.name if m.catch and m.catch.fish else None,
                    "catch_length": m.catch.length if m.catch else None,
                    "created_at": m.created_at.isoformat(),
                }
                for m in movements
                if m.user_id is not None  # Only include user movements
            ],
        }


@router.post("/events/{event_id}/recalculate")
async def recalculate_leaderboard(
    event_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger leaderboard refresh for live clients and update DB ranks.

    Since the leaderboard API always calculates fresh from DB, this endpoint
    is mainly useful for:
    1. Notifying SSE clients to refresh their view
    2. Updating EventScoreboard.rank in DB (for exports/reports)
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Queue Celery task to update DB ranks and notify SSE clients
    queue_leaderboard_recalculation(event_id, "manual_recalculate")

    return {
        "message": "Leaderboard refresh triggered",
        "event_id": event_id,
    }


@router.get("/events/{event_id}/user/{user_id}/details")
async def get_user_catch_details(
    event_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed catch breakdown for a user in an event.

    Shows:
    - Scoring catches with points
    - Not-scoring catches (exceeded slot limit)
    - Rejected catches with reasons

    This is a PUBLIC endpoint for viewing catch details.
    Calculated fresh from DB for reliability.
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if event.is_team_event:
        raise HTTPException(
            status_code=400,
            detail="This is a team event. Use /team/{team_id}/details endpoint instead."
        )

    # Return basic info while cache populates
    # Get user's catches
    catches_query = (
        select(Catch)
        .options(selectinload(Catch.fish))
        .where(
            Catch.event_id == event_id,
            Catch.user_id == user_id,
        )
        .order_by(Catch.length.desc())
    )
    catches_result = await db.execute(catches_query)
    catches = catches_result.scalars().all()

    # Get user info
    user_query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == user_id)
    )
    user_result = await db.execute(user_query)
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user_name = f"{user.profile.first_name} {user.profile.last_name}" if user.profile else f"User {user_id}"

    approved = [c for c in catches if c.status == CatchStatus.APPROVED.value]
    rejected = [c for c in catches if c.status == CatchStatus.REJECTED.value]

    # Calculate proper scoring with slot limits (not simplified)
    catch_details, scored_count, not_scored_count = await _calculate_catch_details_for_fallback(
        approved, event, db
    )

    return {
        "user_id": user_id,
        "user_name": user_name,
        "is_team_event": False,
        "total_catches": len(approved),
        "scored_catches": scored_count,
        "not_scored_catches": not_scored_count,
        "total_points": sum(c["points"] for c in catch_details if c["is_scored"]),
        "bonus_points": 0,
        "species_count": len(set(c["fish_id"] for c in catch_details if c["is_scored"])),
        "catches": catch_details,
        "rejected_catches": [
            {
                "catch_id": c.id,
                "fish_id": c.fish_id,
                "fish_name": c.fish.name if c.fish else "Unknown",
                "length": c.length,
                "photo_url": c.photo_url,
                "submitted_at": c.submitted_at.isoformat() if c.submitted_at else None,
                "rejected_at": c.validated_at.isoformat() if c.validated_at else None,
                "rejection_reason": c.rejection_reason or "No reason provided",
            }
            for c in rejected
        ],
    }


@router.get("/events/{event_id}/team/{team_id}/details")
async def get_team_catch_details(
    event_id: int,
    team_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed catch breakdown for a team in an event.

    Shows:
    - Scoring catches with points (including who uploaded each catch)
    - Not-scoring catches (exceeded slot limit)
    - Rejected catches with reasons
    - Team member breakdown

    This is a PUBLIC endpoint for viewing catch details.
    Calculated fresh from DB for reliability.
    """
    # Check event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if not event.is_team_event:
        raise HTTPException(
            status_code=400,
            detail="This is not a team event. Use /user/{user_id}/details endpoint instead."
        )

    # Get disqualified users for this event
    disqualified_query = select(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.status == EnrollmentStatus.DISQUALIFIED.value,
    )
    disqualified_result = await db.execute(disqualified_query)
    disqualified_enrollments = disqualified_result.scalars().all()
    disqualified_user_ids = {e.user_id for e in disqualified_enrollments}
    disqualified_info = {
        e.user_id: {
            "reason": e.disqualification_reason,
            "at": e.disqualified_at,
        }
        for e in disqualified_enrollments
    }

    # Get team info with user profiles
    team_query = (
        select(Team)
        .options(
            selectinload(Team.members)
            .selectinload(TeamMember.enrollment)
            .selectinload(EventEnrollment.user)
            .selectinload(UserAccount.profile)
        )
        .where(Team.id == team_id)
    )
    team_result = await db.execute(team_query)
    team = team_result.scalar_one_or_none()

    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Get member user IDs and build member info
    member_user_ids = []
    member_info = {}
    for m in team.members:
        if m.is_active and m.enrollment:
            user_id = m.enrollment.user_id
            member_user_ids.append(user_id)
            user = m.enrollment.user
            user_name = f"{user.profile.first_name} {user.profile.last_name}" if user and user.profile else f"User {user_id}"
            is_dq = user_id in disqualified_user_ids
            dq_info = disqualified_info.get(user_id, {})
            member_info[user_id] = {
                "user_id": user_id,
                "user_name": user_name,
                "role": m.role,
                "total_uploaded": 0,
                "total_scoring": 0,
                "total_rejected": 0,
                "is_disqualified": is_dq,
                "disqualification_reason": dq_info.get("reason") if is_dq else None,
            }

    # Get all catches from team members
    catches_query = (
        select(Catch)
        .options(
            selectinload(Catch.fish),
            selectinload(Catch.user).selectinload(UserAccount.profile),
        )
        .where(
            Catch.event_id == event_id,
            Catch.user_id.in_(member_user_ids),
        )
        .order_by(Catch.length.desc())
    )
    catches_result = await db.execute(catches_query)
    catches = catches_result.scalars().all()

    approved = [c for c in catches if c.status == CatchStatus.APPROVED.value]
    rejected = [c for c in catches if c.status == CatchStatus.REJECTED.value]

    # Count catches per member
    for c in approved:
        if c.user_id in member_info:
            member_info[c.user_id]["total_uploaded"] += 1
    for c in rejected:
        if c.user_id in member_info:
            member_info[c.user_id]["total_rejected"] += 1

    # Build team_members list
    team_members = list(member_info.values())

    # Calculate proper scoring with slot limits (not simplified)
    catch_details, scored_count, not_scored_count = await _calculate_catch_details_for_fallback(
        approved, event, db
    )

    # Build a lookup for catch user info
    catch_user_info = {
        c.id: {
            "uploaded_by_id": c.user_id,
            "uploaded_by_name": f"{c.user.profile.first_name} {c.user.profile.last_name}" if c.user and c.user.profile else f"User {c.user_id}",
            "uploaded_by_initials": _get_initials(f"{c.user.profile.first_name} {c.user.profile.last_name}") if c.user and c.user.profile else "",
        }
        for c in approved
    }

    # Add uploaded_by info to catch_details
    for detail in catch_details:
        user_info = catch_user_info.get(detail["catch_id"], {})
        detail["uploaded_by_id"] = user_info.get("uploaded_by_id")
        detail["uploaded_by_name"] = user_info.get("uploaded_by_name", "")
        detail["uploaded_by_initials"] = user_info.get("uploaded_by_initials", "")

    # Update member scoring counts based on calculated details
    for detail in catch_details:
        if detail["is_scored"] and detail.get("uploaded_by_id") in member_info:
            member_info[detail["uploaded_by_id"]]["total_scoring"] += 1

    # Rebuild team_members list with updated scoring
    team_members = list(member_info.values())

    return {
        "team_id": team_id,
        "team_name": team.name,
        "is_team_event": True,
        "total_catches": len(approved),
        "scored_catches": scored_count,
        "not_scored_catches": not_scored_count,
        "total_points": sum(c["points"] for c in catch_details if c["is_scored"]),
        "bonus_points": 0,
        "species_count": len(set(c["fish_id"] for c in catch_details if c["is_scored"])),
        "catches": catch_details,
        "rejected_catches": [
            {
                "catch_id": c.id,
                "fish_id": c.fish_id,
                "fish_name": c.fish.name if c.fish else "Unknown",
                "length": c.length,
                "photo_url": c.photo_url,
                "submitted_at": c.submitted_at.isoformat() if c.submitted_at else None,
                "rejected_at": c.validated_at.isoformat() if c.validated_at else None,
                "rejection_reason": c.rejection_reason or "No reason provided",
                "uploaded_by_id": c.user_id,
                "uploaded_by_name": f"{c.user.profile.first_name} {c.user.profile.last_name}" if c.user and c.user.profile else f"User {c.user_id}",
            }
            for c in rejected
        ],
        "team_members": team_members,
    }


def _get_initials(name: str) -> str:
    """Get initials from a full name."""
    import re
    if not name:
        return ""
    name_parts = re.split(r'[\s\-_]+', name)
    initials = '.'.join([part[0].upper() for part in name_parts if part])
    return initials + '.' if initials else ""
