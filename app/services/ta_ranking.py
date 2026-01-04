"""Trout Area (TA) Ranking & Standings Service.

This service handles:
- Calculating match outcomes and points
- Updating qualifier standings
- Tracking ranking movements
- Generating knockout bracket placements
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.trout_area import (
    TAPointsRule,
    TAEventSettings,
    TAMatch,
    TAGameCard,
    TAQualifierStanding,
    TAMatchOutcome,
    TATournamentPhase,
    TAMatchStatus,
    TAGameCardStatus,
)
from app.models.user import UserAccount
from app.models.team import Team, TeamMember
from app.models.enrollment import EventEnrollment


@dataclass
class RankingMovement:
    """Represents a single ranking movement."""
    user_id: int
    user_name: str
    previous_rank: Optional[int]
    current_rank: int
    change: int  # positive = improved, negative = dropped
    is_new_leader: bool
    total_points: Decimal


@dataclass
class MatchResult:
    """Result of calculating a match outcome."""
    player_a_catches: int
    player_b_catches: int
    player_a_outcome: TAMatchOutcome
    player_b_outcome: TAMatchOutcome
    player_a_points: Decimal
    player_b_points: Decimal


class TARankingService:
    """Service for managing TA rankings and standings."""

    # Map each outcome code to the relevant stat field (from old code)
    OUTCOME_MAP = {
        'V': 'victories',
        'T': 'ties_with_fish',
        'T0': 'ties_without_fish',
        'L': 'losses_with_fish',
        'L0': 'losses_without_fish',
    }

    def __init__(self, db: AsyncSession):
        self.db = db
        self._points_rules: dict[str, Decimal] = {}

    async def _load_points_rules(self) -> None:
        """Load points rules from database."""
        if self._points_rules:
            return

        query = select(TAPointsRule).where(TAPointsRule.is_active == True)
        result = await self.db.execute(query)
        rules = result.scalars().all()

        for rule in rules:
            self._points_rules[rule.code] = rule.points

    def _get_points(self, outcome: TAMatchOutcome) -> Decimal:
        """Get points for a match outcome."""
        return self._points_rules.get(outcome.value, Decimal("0"))

    async def calculate_match_outcome(
        self,
        match: TAMatch,
    ) -> MatchResult:
        """
        Calculate the outcome of a match based on game cards.

        Rules:
        - Victory (V): More catches than opponent (3.0 points)
        - Tie with fish (T): Same catches, both > 0 (1.5 points each)
        - Tie no fish (T0): Both have 0 catches (1.0 points each)
        - Loss with fish (L): Fewer catches but > 0 (0.5 points)
        - Loss no fish (L0): 0 catches and opponent has more (0.0 points)
        """
        await self._load_points_rules()

        # Get validated game cards
        query = (
            select(TAGameCard)
            .where(
                TAGameCard.match_id == match.id,
                TAGameCard.status == TAGameCardStatus.VALIDATED.value,
            )
        )
        result = await self.db.execute(query)
        cards = result.scalars().all()

        # Sum catches by player
        player_a_catches = sum(
            card.total_catches for card in cards
            if card.user_id == match.competitor_a_id
        )
        player_b_catches = sum(
            card.total_catches for card in cards
            if card.user_id == match.competitor_b_id
        )

        # Determine outcomes
        if player_a_catches > player_b_catches:
            # Player A wins
            player_a_outcome = TAMatchOutcome.VICTORY
            player_b_outcome = (
                TAMatchOutcome.LOSS_WITH_FISH
                if player_b_catches > 0
                else TAMatchOutcome.LOSS_NO_FISH
            )
        elif player_b_catches > player_a_catches:
            # Player B wins
            player_b_outcome = TAMatchOutcome.VICTORY
            player_a_outcome = (
                TAMatchOutcome.LOSS_WITH_FISH
                if player_a_catches > 0
                else TAMatchOutcome.LOSS_NO_FISH
            )
        else:
            # Tie
            if player_a_catches > 0:
                player_a_outcome = TAMatchOutcome.TIE_WITH_FISH
                player_b_outcome = TAMatchOutcome.TIE_WITH_FISH
            else:
                player_a_outcome = TAMatchOutcome.TIE_NO_FISH
                player_b_outcome = TAMatchOutcome.TIE_NO_FISH

        return MatchResult(
            player_a_catches=player_a_catches,
            player_b_catches=player_b_catches,
            player_a_outcome=player_a_outcome,
            player_b_outcome=player_b_outcome,
            player_a_points=self._get_points(player_a_outcome),
            player_b_points=self._get_points(player_b_outcome),
        )

    async def finalize_match(
        self,
        match: TAMatch,
    ) -> tuple[TAMatch, list[RankingMovement]]:
        """
        Finalize a match and update standings.

        Returns the updated match and any ranking movements.
        """
        # Calculate outcome
        result = await self.calculate_match_outcome(match)

        # Update match record
        match.competitor_a_catches = result.player_a_catches
        match.competitor_b_catches = result.player_b_catches
        match.competitor_a_outcome_code = result.player_a_outcome.value
        match.competitor_b_outcome_code = result.player_b_outcome.value
        match.competitor_a_points = result.player_a_points
        match.competitor_b_points = result.player_b_points
        match.status = TAMatchStatus.COMPLETED.value
        match.completed_at = datetime.now(timezone.utc)

        await self.db.flush()

        # Update standings for both players
        movements = []

        if match.competitor_a_id:
            movement_a = await self._update_standing(
                event_id=match.event_id,
                user_id=match.competitor_a_id,
                points=result.player_a_points,
                catches=result.player_a_catches,
                outcome=result.player_a_outcome,
            )
            if movement_a:
                movements.append(movement_a)

        if match.competitor_b_id:
            movement_b = await self._update_standing(
                event_id=match.event_id,
                user_id=match.competitor_b_id,
                points=result.player_b_points,
                catches=result.player_b_catches,
                outcome=result.player_b_outcome,
            )
            if movement_b:
                movements.append(movement_b)

        # Recalculate all ranks
        await self._recalculate_ranks(match.event_id)

        return match, movements

    async def _update_standing(
        self,
        event_id: int,
        user_id: int,
        points: Decimal,
        catches: int,
        outcome: TAMatchOutcome,
    ) -> Optional[RankingMovement]:
        """Update or create a standing record for a user."""
        # Get existing standing
        query = select(TAQualifierStanding).where(
            TAQualifierStanding.event_id == event_id,
            TAQualifierStanding.user_id == user_id,
        )
        result = await self.db.execute(query)
        standing = result.scalar_one_or_none()

        previous_rank = standing.rank if standing else None

        if standing is None:
            # Need to get enrollment_id
            from app.models.event import EventEnrollment
            enrollment_query = select(EventEnrollment).where(
                EventEnrollment.event_id == event_id,
                EventEnrollment.user_id == user_id,
                EventEnrollment.status == "approved",
            )
            enr_result = await self.db.execute(enrollment_query)
            enrollment = enr_result.scalar_one_or_none()
            if not enrollment:
                return None  # No enrollment

            # Create new standing
            standing = TAQualifierStanding(
                event_id=event_id,
                user_id=user_id,
                enrollment_id=enrollment.id,
                rank=0,  # Will be calculated
                total_points=points,
                total_fish_caught=catches,
                total_matches=1,
                total_victories=1 if outcome == TAMatchOutcome.VICTORY else 0,
                total_ties=1 if outcome in [TAMatchOutcome.TIE_WITH_FISH, TAMatchOutcome.TIE_NO_FISH] else 0,
                total_losses=1 if outcome in [TAMatchOutcome.LOSS_WITH_FISH, TAMatchOutcome.LOSS_NO_FISH] else 0,
                # Detailed breakdown for tiebreakers
                ties_with_fish=1 if outcome == TAMatchOutcome.TIE_WITH_FISH else 0,
                ties_without_fish=1 if outcome == TAMatchOutcome.TIE_NO_FISH else 0,
                losses_with_fish=1 if outcome == TAMatchOutcome.LOSS_WITH_FISH else 0,
                losses_without_fish=1 if outcome == TAMatchOutcome.LOSS_NO_FISH else 0,
            )
            self.db.add(standing)
        else:
            # Update existing
            standing.total_points += points
            standing.total_fish_caught += catches
            standing.total_matches += 1

            if outcome == TAMatchOutcome.VICTORY:
                standing.total_victories += 1
            elif outcome == TAMatchOutcome.TIE_WITH_FISH:
                standing.total_ties += 1
                standing.ties_with_fish += 1
            elif outcome == TAMatchOutcome.TIE_NO_FISH:
                standing.total_ties += 1
                standing.ties_without_fish += 1
            elif outcome == TAMatchOutcome.LOSS_WITH_FISH:
                standing.total_losses += 1
                standing.losses_with_fish += 1
            elif outcome == TAMatchOutcome.LOSS_NO_FISH:
                standing.total_losses += 1
                standing.losses_without_fish += 1

            standing.updated_at = datetime.now(timezone.utc)

        await self.db.flush()

        # Get user name for movement record
        user_query = (
            select(UserAccount)
            .options(selectinload(UserAccount.profile))
            .where(UserAccount.id == user_id)
        )
        user_result = await self.db.execute(user_query)
        user = user_result.scalar_one_or_none()
        user_name = f"{user.profile.first_name} {user.profile.last_name}".strip() if user and user.profile else f"User {user_id}"

        # Return movement (rank change will be calculated later)
        return RankingMovement(
            user_id=user_id,
            user_name=user_name,
            previous_rank=previous_rank,
            current_rank=0,  # Will be updated
            change=0,  # Will be updated
            is_new_leader=False,  # Will be updated
            total_points=standing.total_points,
        )

    async def _recalculate_ranks(self, event_id: int) -> list[RankingMovement]:
        """
        Recalculate all ranks for an event.

        Ranking is based on (7 tiebreakers):
        1. Total points (desc) - primary
        2. Total fish caught (desc)
        3. Number of victories (desc)
        4. Ties with fish (desc)
        5. Ties without fish (desc)
        6. Losses with fish (asc - fewer is better)
        7. Losses without fish (asc - fewer is better)
        """
        query = (
            select(TAQualifierStanding)
            .options(selectinload(TAQualifierStanding.user).selectinload(UserAccount.profile))
            .where(TAQualifierStanding.event_id == event_id)
            .order_by(
                TAQualifierStanding.total_points.desc(),
                TAQualifierStanding.total_fish_caught.desc(),
                TAQualifierStanding.total_victories.desc(),
                TAQualifierStanding.ties_with_fish.desc(),
                TAQualifierStanding.ties_without_fish.desc(),
                TAQualifierStanding.losses_with_fish.asc(),
                TAQualifierStanding.losses_without_fish.asc(),
            )
        )
        result = await self.db.execute(query)
        standings = result.scalars().all()

        movements = []
        previous_leader = None

        # Get previous leader
        for s in standings:
            if s.rank == 1:
                previous_leader = s.user_id
                break

        # Assign new ranks
        for i, standing in enumerate(standings, 1):
            previous_rank = standing.rank
            standing.rank = i
            standing.updated_at = datetime.now(timezone.utc)

            change = (previous_rank - i) if previous_rank else i
            is_new_leader = (i == 1 and standing.user_id != previous_leader)

            user_name = (
                f"{standing.user.profile.first_name} {standing.user.profile.last_name}".strip()
                if standing.user and standing.user.profile
                else f"User {standing.user_id}"
            )

            if previous_rank != i:
                movements.append(RankingMovement(
                    user_id=standing.user_id,
                    user_name=user_name,
                    previous_rank=previous_rank,
                    current_rank=i,
                    change=change,
                    is_new_leader=is_new_leader,
                    total_points=standing.total_points,
                ))

        return movements

    async def get_standings(
        self,
        event_id: int,
        limit: Optional[int] = None,
    ) -> list[TAQualifierStanding]:
        """Get current standings for an event."""
        query = (
            select(TAQualifierStanding)
            .options(selectinload(TAQualifierStanding.user).selectinload(UserAccount.profile))
            .where(TAQualifierStanding.event_id == event_id)
            .order_by(TAQualifierStanding.rank)
        )

        if limit:
            query = query.limit(limit)

        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_qualification_cutoff(
        self,
        event_id: int,
    ) -> tuple[list[TAQualifierStanding], list[TAQualifierStanding]]:
        """
        Get qualified and requalification participants.

        Returns:
            Tuple of (qualified_list, requalification_list)
        """
        # Get settings
        settings_query = select(TAEventSettings).where(TAEventSettings.event_id == event_id)
        settings_result = await self.db.execute(settings_query)
        settings = settings_result.scalar_one_or_none()

        if not settings:
            return [], []

        standings = await self.get_standings(event_id)

        qualified = standings[:settings.qualification_top_n]

        if settings.enable_requalification:
            requalification = standings[
                settings.qualification_top_n:
                settings.qualification_top_n + settings.requalification_spots
            ]
        else:
            requalification = []

        return qualified, requalification

    async def compute_leg_ranking(
        self,
        event_id: int,
        leg_number: Optional[int] = None,
        phase: Optional[str] = None,
    ) -> list[dict]:
        """
        Build a list of competitor stats (points, captures, W/T/L breakdown),
        sorted by tie-break rules. Cumulative up to `leg_number`.

        Tie-break order (from old code):
        1) points desc
        2) captures desc
        3) victories desc
        4) ties_with_fish desc
        5) ties_without_fish desc
        6) losses_with_fish asc
        7) losses_without_fish asc
        8) direct match if still tied
        """
        from collections import defaultdict
        from functools import cmp_to_key

        # Build query for matches
        query = select(TAMatch).where(TAMatch.event_id == event_id)

        if leg_number is not None:
            query = query.where(TAMatch.leg_number <= leg_number)

        if phase:
            query = query.where(TAMatch.phase == phase)

        # Filter only completed matches
        query = query.where(TAMatch.status == TAMatchStatus.COMPLETED.value)

        result = await self.db.execute(query)
        matches = result.scalars().all()

        # Accumulate stats by competitor
        competitor_stats = defaultdict(lambda: {
            "points": Decimal("0.00"),
            "captures": 0,
            "victories": 0,
            "ties_with_fish": 0,
            "ties_without_fish": 0,
            "losses_with_fish": 0,
            "losses_without_fish": 0,
            "matches_played": 0,
        })

        for match in matches:
            # Process competitor A
            if match.competitor_a_id:
                stats = competitor_stats[match.competitor_a_id]
                stats["points"] += match.competitor_a_points or Decimal("0.00")
                stats["captures"] += match.competitor_a_catches or 0
                stats["matches_played"] += 1

                outcome_code = match.competitor_a_outcome_code
                if outcome_code in self.OUTCOME_MAP:
                    field_name = self.OUTCOME_MAP[outcome_code]
                    stats[field_name] += 1

            # Process competitor B
            if match.competitor_b_id:
                stats = competitor_stats[match.competitor_b_id]
                stats["points"] += match.competitor_b_points or Decimal("0.00")
                stats["captures"] += match.competitor_b_catches or 0
                stats["matches_played"] += 1

                outcome_code = match.competitor_b_outcome_code
                if outcome_code in self.OUTCOME_MAP:
                    field_name = self.OUTCOME_MAP[outcome_code]
                    stats[field_name] += 1

        # Convert to list
        comp_list = []
        for user_id, stats in competitor_stats.items():
            comp_list.append({"user_id": user_id, **stats})

        # Get user names
        if comp_list:
            user_ids = [c["user_id"] for c in comp_list]
            users_query = (
                select(UserAccount)
                .options(selectinload(UserAccount.profile))
                .where(UserAccount.id.in_(user_ids))
            )
            users_result = await self.db.execute(users_query)
            users = {u.id: u for u in users_result.scalars().all()}

            for comp in comp_list:
                user = users.get(comp["user_id"])
                if user and user.profile:
                    comp["user_name"] = f"{user.profile.first_name} {user.profile.last_name}".strip()
                    comp["user_avatar"] = user.profile.profile_picture_url
                else:
                    comp["user_name"] = f"User {comp['user_id']}"
                    comp["user_avatar"] = None

        # Sort using tie-break rules
        def compare_competitors(a, b):
            # 1) points desc
            if a["points"] != b["points"]:
                return -1 if a["points"] > b["points"] else 1
            # 2) captures desc
            if a["captures"] != b["captures"]:
                return -1 if a["captures"] > b["captures"] else 1
            # 3) victories desc
            if a["victories"] != b["victories"]:
                return -1 if a["victories"] > b["victories"] else 1
            # 4) ties_with_fish desc
            if a["ties_with_fish"] != b["ties_with_fish"]:
                return -1 if a["ties_with_fish"] > b["ties_with_fish"] else 1
            # 5) ties_without_fish desc
            if a["ties_without_fish"] != b["ties_without_fish"]:
                return -1 if a["ties_without_fish"] > b["ties_without_fish"] else 1
            # 6) losses_with_fish asc
            if a["losses_with_fish"] != b["losses_with_fish"]:
                return -1 if a["losses_with_fish"] < b["losses_with_fish"] else 1
            # 7) losses_without_fish asc
            if a["losses_without_fish"] != b["losses_without_fish"]:
                return -1 if a["losses_without_fish"] < b["losses_without_fish"] else 1
            return 0

        comp_list.sort(key=cmp_to_key(compare_competitors))

        # Assign ranks (shared ranks for identical stats)
        self._assign_ranks(comp_list)

        return comp_list

    def _assign_ranks(self, comp_list: list[dict]) -> None:
        """
        If stats are identical, they share the same rank.
        Next rank jumps by the number of tied positions.
        """
        if not comp_list:
            return

        last_rank = 1
        last_stats = None

        for i, comp in enumerate(comp_list):
            current_stats = (
                comp["points"],
                comp["captures"],
                comp["victories"],
                comp["ties_with_fish"],
                comp["ties_without_fish"],
                comp["losses_with_fish"],
                comp["losses_without_fish"],
            )
            if i == 0:
                comp["rank"] = 1
                last_stats = current_stats
                continue

            if current_stats == last_stats:
                comp["rank"] = last_rank
            else:
                comp["rank"] = i + 1
                last_rank = i + 1
            last_stats = current_stats

    async def get_leg_matches(
        self,
        event_id: int,
        leg_number: int,
        phase: Optional[str] = None,
    ) -> list[dict]:
        """Get all matches for a specific leg with A vs B details."""
        query = (
            select(TAMatch)
            .where(
                TAMatch.event_id == event_id,
                TAMatch.leg_number == leg_number,
            )
            .order_by(TAMatch.match_number)
        )

        if phase:
            query = query.where(TAMatch.phase == phase)

        result = await self.db.execute(query)
        matches = result.scalars().all()

        # Get user info
        user_ids = set()
        for m in matches:
            if m.competitor_a_id:
                user_ids.add(m.competitor_a_id)
            if m.competitor_b_id:
                user_ids.add(m.competitor_b_id)

        users = {}
        if user_ids:
            users_query = (
                select(UserAccount)
                .options(selectinload(UserAccount.profile))
                .where(UserAccount.id.in_(user_ids))
            )
            users_result = await self.db.execute(users_query)
            users = {u.id: u for u in users_result.scalars().all()}

        match_details = []
        for m in matches:
            user_a = users.get(m.competitor_a_id)
            user_b = users.get(m.competitor_b_id)

            match_details.append({
                "match_id": m.id,
                "leg_number": m.leg_number,
                "round_number": m.round_number,
                "match_number": m.match_number,
                "competitor_a_id": m.competitor_a_id,
                "competitor_a_name": f"{user_a.profile.first_name} {user_a.profile.last_name}".strip() if user_a and user_a.profile else None,
                "competitor_a_catches": m.competitor_a_catches or 0,
                "competitor_a_outcome": m.competitor_a_outcome_code,
                "competitor_a_points": m.competitor_a_points or Decimal("0"),
                "competitor_b_id": m.competitor_b_id,
                "competitor_b_name": f"{user_b.profile.first_name} {user_b.profile.last_name}".strip() if user_b and user_b.profile else None,
                "competitor_b_catches": m.competitor_b_catches or 0,
                "competitor_b_outcome": m.competitor_b_outcome_code,
                "competitor_b_points": m.competitor_b_points or Decimal("0"),
                "status": m.status,
                "is_ghost_match": m.is_ghost_match,
            })

        return match_details

    async def get_event_statistics(
        self,
        event_id: int,
    ) -> dict:
        """Get event-level statistics for TA competition."""
        # Get settings
        settings_query = select(TAEventSettings).where(TAEventSettings.event_id == event_id)
        settings_result = await self.db.execute(settings_query)
        settings = settings_result.scalar_one_or_none()

        # Count UNIQUE participants from lineups (not all lineup entries)
        from app.models.trout_area import TALineup
        lineup_query = select(func.count(func.distinct(TALineup.user_id))).where(
            TALineup.event_id == event_id,
            TALineup.is_ghost == False,
            TALineup.user_id.isnot(None),
        )
        lineup_result = await self.db.execute(lineup_query)
        total_participants = lineup_result.scalar() or 0

        # Count matches
        match_count_query = select(func.count()).select_from(TAMatch).where(
            TAMatch.event_id == event_id
        )
        match_result = await self.db.execute(match_count_query)
        total_matches = match_result.scalar() or 0

        completed_count_query = select(func.count()).select_from(TAMatch).where(
            TAMatch.event_id == event_id,
            TAMatch.status == TAMatchStatus.COMPLETED.value,
        )
        completed_result = await self.db.execute(completed_count_query)
        completed_matches = completed_result.scalar() or 0

        # Total catches
        catches_query = select(
            func.coalesce(func.sum(TAMatch.competitor_a_catches), 0) +
            func.coalesce(func.sum(TAMatch.competitor_b_catches), 0)
        ).where(TAMatch.event_id == event_id)
        catches_result = await self.db.execute(catches_query)
        total_catches = catches_result.scalar() or 0

        # Get current phase info from settings
        current_phase = "qualifier"
        current_leg = 1
        total_legs = 5  # Default

        if settings:
            add_rules = settings.additional_rules or {}
            current_phase = add_rules.get("current_phase", "qualifier")
            current_leg = add_rules.get("current_round", 1)
            total_legs = settings.number_of_legs or 5

        # Get top scorer (by points)
        rankings = await self.compute_leg_ranking(event_id)
        top_scorer = None
        most_catches = None
        most_victories = None

        def _transform_ranking(r: dict) -> dict:
            """Transform ranking dict to match frontend expected field names."""
            return {
                "user_id": r["user_id"],
                "user_name": r.get("user_name"),
                "user_avatar": r.get("user_avatar"),
                "total_points": float(r["points"]),  # Frontend expects total_points
                "total_catches": r["captures"],  # Frontend expects total_catches
                "victories": r.get("victories", 0),
            }

        if rankings:
            # Top scorer (by points)
            top_scorer = _transform_ranking(rankings[0])

            # Most catches
            catches_sorted = sorted(rankings, key=lambda x: -x["captures"])
            if catches_sorted:
                most_catches = _transform_ranking(catches_sorted[0])

            # Most victories
            victories_sorted = sorted(rankings, key=lambda x: -x["victories"])
            if victories_sorted:
                most_victories = _transform_ranking(victories_sorted[0])

        # Calculate average catches per match (total for both players)
        average_catches_per_match = total_catches / completed_matches if completed_matches > 0 else 0.0

        return {
            "event_id": event_id,
            "total_participants": total_participants,
            "total_matches": total_matches,
            "completed_matches": completed_matches,
            "total_catches": total_catches,
            "average_catches_per_match": round(average_catches_per_match, 1),
            "top_scorer": top_scorer,
            "most_catches": most_catches,
            "most_victories": most_victories,
            "current_phase": current_phase,
            "current_leg": current_leg,
            "total_legs": total_legs,
        }

    async def compute_team_ranking(
        self,
        event_id: int,
        phase: Optional[str] = None,
    ) -> list[dict]:
        """
        Compute team rankings for TA team events.

        Aggregates individual member standings into team scores.
        Scoring methods:
        - sum: Total of all member points
        - average: Average of member points
        - best_n: Sum of top N member scores (N = team_size or all)

        Tiebreakers (in order):
        1) Team points (desc)
        2) Team total captures (desc)
        3) Team victories (desc)
        4) Team ties with fish (desc)
        5) First completed match time (asc)
        """
        from collections import defaultdict
        from functools import cmp_to_key

        # Get event settings
        settings_query = select(TAEventSettings).where(TAEventSettings.event_id == event_id)
        settings_result = await self.db.execute(settings_query)
        settings = settings_result.scalar_one_or_none()

        if not settings or not settings.is_team_event:
            return []

        scoring_method = settings.team_scoring_method or "sum"
        team_size = settings.team_size or 2

        # Get teams for this event
        teams_query = (
            select(Team)
            .options(selectinload(Team.members).selectinload(TeamMember.enrollment))
            .where(Team.event_id == event_id, Team.is_active == True)
        )
        teams_result = await self.db.execute(teams_query)
        teams = teams_result.scalars().all()

        if not teams:
            return []

        # Build user_id -> team_id mapping
        user_to_team: dict[int, int] = {}
        team_members_map: dict[int, list[int]] = defaultdict(list)

        for team in teams:
            for member in team.members:
                if member.is_active and member.enrollment:
                    user_id = member.enrollment.user_id
                    if user_id:
                        user_to_team[user_id] = team.id
                        team_members_map[team.id].append(user_id)

        # Get individual rankings
        individual_rankings = await self.compute_leg_ranking(event_id, phase=phase)

        # Map user_id to individual stats
        user_stats: dict[int, dict] = {}
        for r in individual_rankings:
            user_stats[r["user_id"]] = r

        # Aggregate team stats
        team_data: dict[int, dict] = {}

        for team in teams:
            team_id = team.id
            member_user_ids = team_members_map.get(team_id, [])

            member_stats_list = []
            for uid in member_user_ids:
                if uid in user_stats:
                    member_stats_list.append(user_stats[uid])

            if not member_stats_list:
                # Team has no match data yet
                team_data[team_id] = {
                    "team_id": team_id,
                    "team_name": team.name,
                    "team_number": team.team_number,
                    "logo_url": team.logo_url,
                    "points": Decimal("0.00"),
                    "captures": 0,
                    "victories": 0,
                    "ties_with_fish": 0,
                    "ties_without_fish": 0,
                    "losses_with_fish": 0,
                    "losses_without_fish": 0,
                    "matches_played": 0,
                    "member_count": len(member_user_ids),
                    "members": [],
                }
                continue

            # Apply scoring method
            if scoring_method == "average":
                total_points = sum(m["points"] for m in member_stats_list)
                team_points = total_points / len(member_stats_list)
            elif scoring_method == "best_n":
                # Sort by points descending, take top N
                sorted_members = sorted(member_stats_list, key=lambda x: -x["points"])
                top_n = sorted_members[:team_size]
                team_points = sum(m["points"] for m in top_n)
            else:  # sum (default)
                team_points = sum(m["points"] for m in member_stats_list)

            # Sum other stats
            team_captures = sum(m["captures"] for m in member_stats_list)
            team_victories = sum(m["victories"] for m in member_stats_list)
            team_ties_fish = sum(m["ties_with_fish"] for m in member_stats_list)
            team_ties_no_fish = sum(m["ties_without_fish"] for m in member_stats_list)
            team_losses_fish = sum(m["losses_with_fish"] for m in member_stats_list)
            team_losses_no_fish = sum(m["losses_without_fish"] for m in member_stats_list)
            team_matches = sum(m["matches_played"] for m in member_stats_list)

            # Build member details
            member_details = []
            for m in member_stats_list:
                member_details.append({
                    "user_id": m["user_id"],
                    "user_name": m.get("user_name", f"User {m['user_id']}"),
                    "user_avatar": m.get("user_avatar"),
                    "points": float(m["points"]),
                    "captures": m["captures"],
                    "victories": m["victories"],
                    "ties": m["ties_with_fish"] + m["ties_without_fish"],
                    "losses": m["losses_with_fish"] + m["losses_without_fish"],
                    "matches_played": m["matches_played"],
                    "rank": m.get("rank", 0),
                })

            # Sort members by points descending
            member_details.sort(key=lambda x: -x["points"])

            team_data[team_id] = {
                "team_id": team_id,
                "team_name": team.name,
                "team_number": team.team_number,
                "logo_url": team.logo_url,
                "points": team_points,
                "captures": team_captures,
                "victories": team_victories,
                "ties_with_fish": team_ties_fish,
                "ties_without_fish": team_ties_no_fish,
                "losses_with_fish": team_losses_fish,
                "losses_without_fish": team_losses_no_fish,
                "matches_played": team_matches,
                "member_count": len(member_user_ids),
                "members": member_details,
            }

        # Convert to list
        team_list = list(team_data.values())

        # Sort using tiebreaker rules
        def compare_teams(a, b):
            # 1) points desc
            if a["points"] != b["points"]:
                return -1 if a["points"] > b["points"] else 1
            # 2) captures desc
            if a["captures"] != b["captures"]:
                return -1 if a["captures"] > b["captures"] else 1
            # 3) victories desc
            if a["victories"] != b["victories"]:
                return -1 if a["victories"] > b["victories"] else 1
            # 4) ties_with_fish desc
            if a["ties_with_fish"] != b["ties_with_fish"]:
                return -1 if a["ties_with_fish"] > b["ties_with_fish"] else 1
            return 0

        team_list.sort(key=cmp_to_key(compare_teams))

        # Assign ranks
        for i, team in enumerate(team_list, 1):
            team["rank"] = i
            team["points"] = float(team["points"])  # Convert Decimal for JSON

        return team_list


class TSFRankingService:
    """Service for managing TSF rankings and standings."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def recalc_leg_positions(
        self,
        event_id: int,
        day_number: int,
        group_number: int,
        leg_number: int,
    ) -> None:
        """
        Recompute ranks for one group+leg using RANK.AVG style logic.
        1) Query positions for (event, day, group, leg).
        2) Sort descending by fish_count.
        3) Assign RANK.AVG for ties => store in position.position_value.
        """
        from app.models.trout_shore import TSFLegPosition

        query = select(TSFLegPosition).where(
            TSFLegPosition.event_id == event_id,
            TSFLegPosition.day_number == day_number,
            TSFLegPosition.group_number == group_number,
            TSFLegPosition.leg_number == leg_number,
        )
        result = await self.db.execute(query)
        positions = list(result.scalars().all())

        if not positions:
            return

        # Sort descending by fish_count (more fish = better = lower position)
        positions.sort(key=lambda x: x.fish_count, reverse=True)

        # Compute RANK.AVG for ties
        i = 0
        while i < len(positions):
            # Identify tie block
            tie_start = i
            tie_fish = positions[i].fish_count
            while i < len(positions) and positions[i].fish_count == tie_fish:
                i += 1
            tie_size = i - tie_start

            # e.g. if tie_start=0, tie_size=3 => positions 1,2,3 => avg=2.0
            tie_pos_start = tie_start + 1
            tie_pos_end = tie_pos_start + tie_size - 1
            avg_rank = (tie_pos_start + tie_pos_end) / 2.0

            for j in range(tie_start, tie_start + tie_size):
                positions[j].position_value = avg_rank

        await self.db.flush()

    async def get_group_ranking_with_legs(
        self,
        event_id: int,
        day_number: Optional[int] = None,
    ) -> list[dict]:
        """
        Returns group rankings with leg-by-leg breakdown.
        Similar to old code's get_group_ranking_with_legs function.
        """
        from collections import defaultdict
        from app.models.trout_shore import TSFLegPosition

        # Get all positions for the event
        query = select(TSFLegPosition).where(TSFLegPosition.event_id == event_id)
        if day_number is not None:
            query = query.where(TSFLegPosition.day_number == day_number)
        query = query.order_by(TSFLegPosition.group_number, TSFLegPosition.leg_number)

        result = await self.db.execute(query)
        positions = result.scalars().all()

        # Group by group_number
        group_dict: dict[int, list] = defaultdict(list)
        for pos in positions:
            group_dict[pos.group_number].append(pos)

        # Get user info
        user_ids = set(p.user_id for p in positions if p.user_id)
        users = {}
        if user_ids:
            users_query = (
                select(UserAccount)
                .options(selectinload(UserAccount.profile))
                .where(UserAccount.id.in_(user_ids))
            )
            users_result = await self.db.execute(users_query)
            users = {u.id: u for u in users_result.scalars().all()}

        results = []

        for group_num, group_positions in group_dict.items():
            # Build user data for this group
            user_data_map: dict[int, dict] = defaultdict(lambda: {
                "legs_map": {},
                "user_obj": None,
                "is_ghost": False,
            })

            for pos in group_positions:
                uid = pos.user_id
                if uid is None:
                    continue

                if user_data_map[uid]["user_obj"] is None:
                    user_data_map[uid]["user_obj"] = users.get(uid)
                user_data_map[uid]["is_ghost"] = pos.is_ghost

                leg_no = pos.leg_number
                fish = pos.fish_count
                pts = pos.position_value or 0
                user_data_map[uid]["legs_map"][leg_no] = (fish, pts)

            # Build participants list
            participants = []
            for uid, info in user_data_map.items():
                user_obj = info["user_obj"]
                is_ghost = info["is_ghost"]

                if user_obj and user_obj.profile:
                    full_name = f"{user_obj.profile.first_name} {user_obj.profile.last_name}".strip()
                else:
                    full_name = "GHOST" if is_ghost else f"User {uid}"

                # Convert legs_map to list
                legs_details = []
                total_fish = 0
                total_points = 0.0

                for leg_no, (fish, pts) in sorted(info["legs_map"].items()):
                    legs_details.append({
                        "leg_number": leg_no,
                        "fish_caught": fish,
                        "leg_points": pts,
                    })
                    total_fish += fish
                    total_points += pts

                participants.append({
                    "user_id": uid,
                    "user_name": full_name,
                    "is_ghost": is_ghost,
                    "legs": legs_details,
                    "total_fish": total_fish,
                    "total_points": total_points,
                })

            # Sort ascending by total_points (lower is better)
            participants.sort(key=lambda x: x["total_points"])

            # Assign RANK.AVG for ties
            self._assign_rank_avg(participants, "total_points", "group_rank")

            results.append({
                "group_number": group_num,
                "participants": participants,
            })

        # Sort groups by number
        results.sort(key=lambda x: x["group_number"])

        return results

    def _assign_rank_avg(
        self,
        items: list[dict],
        value_key: str,
        rank_key: str,
    ) -> None:
        """Assign RANK.AVG style ranks (tie average)."""
        if not items:
            return

        i = 0
        n = len(items)
        while i < n:
            tie_start = i
            tie_val = items[i][value_key]
            while i < n and items[i][value_key] == tie_val:
                i += 1
            tie_end = i - 1
            tie_size = tie_end - tie_start + 1

            tie_pos_start = tie_start + 1
            tie_pos_end = tie_pos_start + tie_size - 1
            avg_rank = (tie_pos_start + tie_pos_end) / 2.0

            for j in range(tie_start, tie_end + 1):
                items[j][rank_key] = avg_rank

    async def get_final_ranking(
        self,
        event_id: int,
        exclude_ghosts: bool = True,
    ) -> list[dict]:
        """
        Get final ranking across all groups.
        Lower total_points = better rank.
        """
        from collections import defaultdict
        from app.models.trout_shore import TSFLegPosition

        query = select(TSFLegPosition).where(TSFLegPosition.event_id == event_id)
        if exclude_ghosts:
            query = query.where(TSFLegPosition.is_ghost == False)

        result = await self.db.execute(query)
        positions = result.scalars().all()

        # Aggregate by user
        user_fish_map: dict[int, int] = defaultdict(int)
        user_points_map: dict[int, float] = defaultdict(float)
        user_ghost_map: dict[int, bool] = {}
        user_leg_count: dict[int, int] = defaultdict(int)

        for pos in positions:
            if pos.user_id is None:
                continue

            uid = pos.user_id
            user_leg_count[uid] += 1
            user_fish_map[uid] += pos.fish_count
            if pos.position_value is not None:
                user_points_map[uid] += pos.position_value
            user_ghost_map[uid] = pos.is_ghost

        # Get user info
        user_ids = list(user_fish_map.keys())
        users = {}
        if user_ids:
            users_query = (
                select(UserAccount)
                .options(selectinload(UserAccount.profile))
                .where(UserAccount.id.in_(user_ids))
            )
            users_result = await self.db.execute(users_query)
            users = {u.id: u for u in users_result.scalars().all()}

        # Build final list
        final_list = []
        for uid, total_fish in user_fish_map.items():
            total_points = user_points_map[uid]
            is_ghost = user_ghost_map.get(uid, False)
            user_obj = users.get(uid)

            if total_points == 0 and total_fish == 0:
                continue  # Exclude

            if user_obj and user_obj.profile:
                name = f"{user_obj.profile.first_name} {user_obj.profile.last_name}".strip()
            else:
                name = "GHOST" if is_ghost else f"User {uid}"

            final_list.append({
                "user_id": uid,
                "user_name": name,
                "is_ghost": is_ghost,
                "total_fish_caught": total_fish,
                "total_points": total_points,
                "legs_completed": user_leg_count[uid],
            })

        # Sort by total_points ascending, tiebreak by fish descending
        final_list.sort(key=lambda x: (x["total_points"], -x["total_fish_caught"]))

        # Assign ranks
        for i, item in enumerate(final_list, start=1):
            item["final_rank"] = i

        return final_list

    async def get_event_statistics(
        self,
        event_id: int,
    ) -> dict:
        """
        Get event-level statistics, excluding ghosts.
        Similar to old code's get_event_statistics_no_ghosts.
        """
        from collections import defaultdict
        from app.models.trout_shore import TSFLegPosition

        query = (
            select(TSFLegPosition)
            .where(
                TSFLegPosition.event_id == event_id,
                TSFLegPosition.is_ghost == False,
                TSFLegPosition.user_id.isnot(None),
            )
        )
        result = await self.db.execute(query)
        positions = result.scalars().all()

        group_points: dict[int, float] = defaultdict(float)
        group_fish: dict[int, int] = defaultdict(int)
        user_points: dict[int, float] = defaultdict(float)
        user_fish: dict[int, int] = defaultdict(int)
        total_fish = 0

        for pos in positions:
            gnum = pos.group_number
            uid = pos.user_id
            pts = pos.position_value or 0
            fish = pos.fish_count

            group_points[gnum] += pts
            group_fish[gnum] += fish
            user_points[uid] += pts
            user_fish[uid] += fish
            total_fish += fish

        # Find best group by points (lower is better)
        best_group_points = None
        best_points_val = float("inf")
        for gnum, val in group_points.items():
            if val < best_points_val:
                best_points_val = val
                best_group_points = gnum

        # Best group by fish (higher is better)
        best_group_fish = None
        best_fish_val = -1
        for gnum, val in group_fish.items():
            if val > best_fish_val:
                best_fish_val = val
                best_group_fish = gnum

        # Best participant by points (lower is better)
        best_user_points = None
        best_user_points_val = float("inf")
        for uid, val in user_points.items():
            if val < best_user_points_val:
                best_user_points_val = val
                best_user_points = uid

        # Best participant by fish (higher is better)
        best_user_fish = None
        best_user_fish_val = -1
        for uid, val in user_fish.items():
            if val > best_user_fish_val:
                best_user_fish_val = val
                best_user_fish = uid

        # Get user names
        user_ids = [u for u in [best_user_points, best_user_fish] if u is not None]
        users = {}
        if user_ids:
            users_query = (
                select(UserAccount)
                .options(selectinload(UserAccount.profile))
                .where(UserAccount.id.in_(user_ids))
            )
            users_result = await self.db.execute(users_query)
            users = {u.id: u for u in users_result.scalars().all()}

        # Build result
        best_participant_points = None
        if best_user_points is not None:
            user_obj = users.get(best_user_points)
            name = f"{user_obj.profile.first_name} {user_obj.profile.last_name}".strip() if user_obj and user_obj.profile else f"User {best_user_points}"
            best_participant_points = {
                "user_id": best_user_points,
                "user_name": name,
                "total_points": best_user_points_val,
            }

        best_participant_fish = None
        if best_user_fish is not None:
            user_obj = users.get(best_user_fish)
            name = f"{user_obj.profile.first_name} {user_obj.profile.last_name}".strip() if user_obj and user_obj.profile else f"User {best_user_fish}"
            best_participant_fish = {
                "user_id": best_user_fish,
                "user_name": name,
                "total_fish": best_user_fish_val,
            }

        return {
            "event_id": event_id,
            "total_participants": len(user_points),
            "total_groups": len(group_points),
            "total_fish_caught": total_fish,
            "best_group_by_points": best_group_points,
            "best_group_by_points_value": best_points_val if best_group_points else None,
            "best_group_by_fish": best_group_fish,
            "best_group_by_fish_value": best_fish_val if best_group_fish else None,
            "best_participant_by_points": best_participant_points,
            "best_participant_by_fish": best_participant_fish,
        }

    async def calculate_day_standings(
        self,
        event_id: int,
        day_id: int,
    ) -> list[RankingMovement]:
        """
        Calculate standings for a competition day.

        Aggregates leg positions into day standings.
        Lower position points = better (1st place = 1 point, 2nd = 2, etc.)
        """
        from app.models.trout_shore import (
            TSFEventSettings,
            TSFDay,
            TSFLeg,
            TSFLegPosition,
            TSFDayStanding,
        )

        # Get settings
        settings_query = select(TSFEventSettings).where(TSFEventSettings.event_id == event_id)
        settings_result = await self.db.execute(settings_query)
        settings = settings_result.scalar_one_or_none()

        # Get day
        day_query = select(TSFDay).where(TSFDay.id == day_id)
        day_result = await self.db.execute(day_query)
        day = day_result.scalar_one_or_none()

        if not settings or not day:
            return []

        # Get all positions for this day
        positions_query = (
            select(TSFLegPosition)
            .where(
                TSFLegPosition.event_id == event_id,
                TSFLegPosition.day_number == day.day_number,
            )
        )
        positions_result = await self.db.execute(positions_query)
        positions = positions_result.scalars().all()

        # Group by user
        user_data: dict[int, dict] = {}

        for pos in positions:
            if pos.user_id is None:
                continue

            if pos.user_id not in user_data:
                user_data[pos.user_id] = {
                    "total_position_points": 0,
                    "legs_completed": 0,
                    "first_places": 0,
                    "second_places": 0,
                    "third_places": 0,
                    "best_single_leg": None,
                    "worst_single_leg": None,
                    "total_fish_count": 0,
                    "total_length": 0.0,
                    "leg_positions": {},
                    "group_number": pos.group_number,
                }

            data = user_data[pos.user_id]
            data["total_position_points"] += pos.position_value
            data["legs_completed"] += 1
            data["total_fish_count"] += pos.fish_count or 0
            data["total_length"] += pos.total_length or 0.0
            data["leg_positions"][str(pos.leg_number)] = pos.position_value

            # Track placements
            if pos.position_value == 1:
                data["first_places"] += 1
            elif pos.position_value == 2:
                data["second_places"] += 1
            elif pos.position_value == 3:
                data["third_places"] += 1

            # Track best/worst
            if data["best_single_leg"] is None or pos.position_value < data["best_single_leg"]:
                data["best_single_leg"] = pos.position_value
            if data["worst_single_leg"] is None or pos.position_value > data["worst_single_leg"]:
                data["worst_single_leg"] = pos.position_value

        # Create/update day standings
        for user_id, data in user_data.items():
            standing_query = select(TSFDayStanding).where(
                TSFDayStanding.event_id == event_id,
                TSFDayStanding.day_id == day_id,
                TSFDayStanding.user_id == user_id,
            )
            standing_result = await self.db.execute(standing_query)
            standing = standing_result.scalar_one_or_none()

            if standing is None:
                standing = TSFDayStanding(
                    event_id=event_id,
                    day_id=day_id,
                    day_number=day.day_number,
                    user_id=user_id,
                    group_number=data["group_number"],
                )
                self.db.add(standing)

            standing.total_position_points = data["total_position_points"]
            standing.legs_completed = data["legs_completed"]
            standing.first_places = data["first_places"]
            standing.second_places = data["second_places"]
            standing.third_places = data["third_places"]
            standing.best_single_leg = data["best_single_leg"]
            standing.worst_single_leg = data["worst_single_leg"]
            standing.total_fish_count = data["total_fish_count"]
            standing.total_length = data["total_length"]
            standing.leg_positions = data["leg_positions"]
            standing.updated_at = datetime.now(timezone.utc)

        await self.db.flush()

        # Calculate sector ranks within each group
        # Lower total_position_points = better rank
        movements = await self._calculate_sector_ranks(event_id, day_id)

        return movements

    async def _calculate_sector_ranks(
        self,
        event_id: int,
        day_id: int,
    ) -> list[RankingMovement]:
        """Calculate sector ranks and overall ranks for a day."""
        from app.models.trout_shore import TSFDayStanding

        # Get all standings for this day
        query = (
            select(TSFDayStanding)
            .options(selectinload(TSFDayStanding.user).selectinload(UserAccount.profile))
            .where(
                TSFDayStanding.event_id == event_id,
                TSFDayStanding.day_id == day_id,
            )
            .order_by(TSFDayStanding.total_position_points)  # Lower is better
        )
        result = await self.db.execute(query)
        standings = result.scalars().all()

        # Group by sector
        sectors: dict[int, list] = {}
        for standing in standings:
            if standing.group_number not in sectors:
                sectors[standing.group_number] = []
            sectors[standing.group_number].append(standing)

        movements = []

        # Calculate sector ranks
        for group_num, group_standings in sectors.items():
            # Sort by points (lower is better), then tiebreakers
            group_standings.sort(key=lambda s: (
                s.total_position_points,
                -s.first_places,  # More first places is better
                -s.total_fish_count,
            ))

            for i, standing in enumerate(group_standings, 1):
                previous_rank = standing.sector_rank
                standing.sector_rank = i
                standing.updated_at = datetime.now(timezone.utc)

        # Calculate overall ranks
        all_standings = list(standings)
        all_standings.sort(key=lambda s: (
            s.total_position_points,
            -s.first_places,
            -s.total_fish_count,
        ))

        for i, standing in enumerate(all_standings, 1):
            previous_overall = standing.overall_rank
            standing.overall_rank = i

            if previous_overall != i:
                user_name = (
                    f"{standing.user.profile.first_name} {standing.user.profile.last_name}".strip()
                    if standing.user and standing.user.profile
                    else f"User {standing.user_id}"
                )

                change = (previous_overall - i) if previous_overall else i
                movements.append(RankingMovement(
                    user_id=standing.user_id,
                    user_name=user_name,
                    previous_rank=previous_overall,
                    current_rank=i,
                    change=change,
                    is_new_leader=(i == 1 and previous_overall != 1),
                    total_points=Decimal(str(standing.total_position_points)),
                ))

        return movements

    async def calculate_final_standings(
        self,
        event_id: int,
    ) -> list[RankingMovement]:
        """
        Calculate final standings across all days.

        Aggregates day standings into final standings.
        """
        from app.models.trout_shore import (
            TSFEventSettings,
            TSFDayStanding,
            TSFFinalStanding,
            TSFLineup,
        )

        # Get settings
        settings_query = select(TSFEventSettings).where(TSFEventSettings.event_id == event_id)
        settings_result = await self.db.execute(settings_query)
        settings = settings_result.scalar_one_or_none()

        if not settings:
            return []

        # Get all day standings
        day_standings_query = (
            select(TSFDayStanding)
            .where(TSFDayStanding.event_id == event_id)
        )
        day_result = await self.db.execute(day_standings_query)
        day_standings = day_result.scalars().all()

        # Group by user
        user_data: dict[int, dict] = {}

        for ds in day_standings:
            if ds.user_id not in user_data:
                user_data[ds.user_id] = {
                    "total_position_points": 0,
                    "days_completed": 0,
                    "legs_completed": 0,
                    "total_first_places": 0,
                    "total_second_places": 0,
                    "total_third_places": 0,
                    "best_single_leg": None,
                    "worst_single_leg": None,
                    "best_day_total": None,
                    "worst_day_total": None,
                    "total_fish_count": 0,
                    "total_length": 0.0,
                    "day_totals": {},
                    "group_number": ds.group_number,
                    "enrollment_id": None,  # Will be looked up
                }

            data = user_data[ds.user_id]
            data["total_position_points"] += ds.total_position_points
            data["days_completed"] += 1
            data["legs_completed"] += ds.legs_completed
            data["total_first_places"] += ds.first_places
            data["total_second_places"] += ds.second_places
            data["total_third_places"] += ds.third_places
            data["total_fish_count"] += ds.total_fish_count
            data["total_length"] += ds.total_length
            data["day_totals"][str(ds.day_number)] = ds.total_position_points

            # Track best/worst leg
            if ds.best_single_leg:
                if data["best_single_leg"] is None or ds.best_single_leg < data["best_single_leg"]:
                    data["best_single_leg"] = ds.best_single_leg
            if ds.worst_single_leg:
                if data["worst_single_leg"] is None or ds.worst_single_leg > data["worst_single_leg"]:
                    data["worst_single_leg"] = ds.worst_single_leg

            # Track best/worst day
            if data["best_day_total"] is None or ds.total_position_points < data["best_day_total"]:
                data["best_day_total"] = ds.total_position_points
            if data["worst_day_total"] is None or ds.total_position_points > data["worst_day_total"]:
                data["worst_day_total"] = ds.total_position_points

        # Get enrollment IDs from lineups
        lineup_query = select(TSFLineup).where(TSFLineup.event_id == event_id)
        lineup_result = await self.db.execute(lineup_query)
        lineups = lineup_result.scalars().all()

        for lineup in lineups:
            if lineup.user_id in user_data:
                user_data[lineup.user_id]["enrollment_id"] = lineup.enrollment_id

        # Create/update final standings
        for user_id, data in user_data.items():
            standing_query = select(TSFFinalStanding).where(
                TSFFinalStanding.event_id == event_id,
                TSFFinalStanding.user_id == user_id,
            )
            standing_result = await self.db.execute(standing_query)
            standing = standing_result.scalar_one_or_none()

            if standing is None:
                standing = TSFFinalStanding(
                    event_id=event_id,
                    user_id=user_id,
                    enrollment_id=data["enrollment_id"] or 0,
                    group_number=data["group_number"],
                )
                self.db.add(standing)

            standing.total_position_points = data["total_position_points"]
            standing.days_completed = data["days_completed"]
            standing.legs_completed = data["legs_completed"]
            standing.total_first_places = data["total_first_places"]
            standing.total_second_places = data["total_second_places"]
            standing.total_third_places = data["total_third_places"]
            standing.best_single_leg = data["best_single_leg"]
            standing.worst_single_leg = data["worst_single_leg"]
            standing.best_day_total = data["best_day_total"]
            standing.worst_day_total = data["worst_day_total"]
            standing.total_fish_count = data["total_fish_count"]
            standing.total_length = data["total_length"]
            standing.day_totals = data["day_totals"]
            standing.updated_at = datetime.now(timezone.utc)

        await self.db.flush()

        # Calculate final ranks
        movements = await self._calculate_final_ranks(event_id)

        return movements

    async def _calculate_final_ranks(
        self,
        event_id: int,
    ) -> list[RankingMovement]:
        """Calculate final ranks for the event."""
        from app.models.trout_shore import TSFFinalStanding

        query = (
            select(TSFFinalStanding)
            .options(selectinload(TSFFinalStanding.user).selectinload(UserAccount.profile))
            .where(TSFFinalStanding.event_id == event_id)
            .order_by(TSFFinalStanding.total_position_points)  # Lower is better
        )
        result = await self.db.execute(query)
        standings = result.scalars().all()

        # Sort by tiebreakers
        standings_list = list(standings)
        standings_list.sort(key=lambda s: (
            s.total_position_points,
            -s.total_first_places,
            -s.total_fish_count,
        ))

        movements = []

        for i, standing in enumerate(standings_list, 1):
            previous_rank = standing.final_rank
            standing.final_rank = i
            standing.updated_at = datetime.now(timezone.utc)

            if previous_rank != i:
                user_name = (
                    f"{standing.user.profile.first_name} {standing.user.profile.last_name}".strip()
                    if standing.user and standing.user.profile
                    else f"User {standing.user_id}"
                )

                change = (previous_rank - i) if previous_rank else i
                movements.append(RankingMovement(
                    user_id=standing.user_id,
                    user_name=user_name,
                    previous_rank=previous_rank,
                    current_rank=i,
                    change=change,
                    is_new_leader=(i == 1 and previous_rank != 1),
                    total_points=Decimal(str(standing.total_position_points)),
                ))

        return movements
