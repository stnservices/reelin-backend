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
        # Cache for direct match results: {(event_id, user_a, user_b): 1/-1/0}
        self._direct_match_cache: dict[tuple[int, int, int], int] = {}

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

    async def _get_direct_match_result(
        self,
        event_id: int,
        user_a_id: int,
        user_b_id: int,
        phase: Optional[str] = None,
    ) -> int:
        """
        Get the direct match result between two users (Story 12.2: 8th tiebreaker).

        Returns:
            1 if user_a won against user_b
            -1 if user_b won against user_a
            0 if no direct match or tie
        """
        # Check cache first
        cache_key = (event_id, user_a_id, user_b_id)
        if cache_key in self._direct_match_cache:
            return self._direct_match_cache[cache_key]

        # Query for direct match between these two users
        query = select(TAMatch).where(
            TAMatch.event_id == event_id,
            TAMatch.status == TAMatchStatus.COMPLETED.value,
            (
                (TAMatch.competitor_a_id == user_a_id) & (TAMatch.competitor_b_id == user_b_id) |
                (TAMatch.competitor_a_id == user_b_id) & (TAMatch.competitor_b_id == user_a_id)
            ),
        )
        if phase:
            query = query.where(TAMatch.phase == phase)

        result = await self.db.execute(query)
        matches = result.scalars().all()

        if not matches:
            self._direct_match_cache[cache_key] = 0
            self._direct_match_cache[(event_id, user_b_id, user_a_id)] = 0
            return 0

        # Sum up wins for each user across all direct matches
        user_a_wins = 0
        user_b_wins = 0

        for match in matches:
            if match.competitor_a_id == user_a_id:
                if match.competitor_a_outcome_code == 'V':
                    user_a_wins += 1
                elif match.competitor_b_outcome_code == 'V':
                    user_b_wins += 1
            else:  # user_b is competitor_a
                if match.competitor_a_outcome_code == 'V':
                    user_b_wins += 1
                elif match.competitor_b_outcome_code == 'V':
                    user_a_wins += 1

        # Determine result
        if user_a_wins > user_b_wins:
            result_val = 1
        elif user_b_wins > user_a_wins:
            result_val = -1
        else:
            result_val = 0

        # Cache both directions
        self._direct_match_cache[cache_key] = result_val
        self._direct_match_cache[(event_id, user_b_id, user_a_id)] = -result_val

        return result_val

    async def is_leg_complete(
        self,
        event_id: int,
        leg_number: int,
        phase: Optional[str] = None,
    ) -> bool:
        """
        Check if all matches in a leg are completed.

        A leg is complete when all game cards for that leg have status = 'completed'
        (validated by both players).

        Args:
            event_id: The event ID
            leg_number: The leg number to check
            phase: Optional phase filter (qualifier, semifinal, etc.)

        Returns:
            True if all matches in the leg are completed, False otherwise
        """
        # Count incomplete game cards in this leg
        query = select(func.count(TAGameCard.id)).where(
            TAGameCard.event_id == event_id,
            TAGameCard.leg_number == leg_number,
            TAGameCard.status != TAGameCardStatus.VALIDATED.value,
        )

        if phase:
            query = query.where(TAGameCard.phase == phase)

        result = await self.db.execute(query)
        incomplete_count = result.scalar() or 0

        # Also check if there are any cards at all for this leg
        total_query = select(func.count(TAGameCard.id)).where(
            TAGameCard.event_id == event_id,
            TAGameCard.leg_number == leg_number,
        )
        if phase:
            total_query = total_query.where(TAGameCard.phase == phase)

        total_result = await self.db.execute(total_query)
        total_count = total_result.scalar() or 0

        # Leg is complete if there are cards and none are incomplete
        return total_count > 0 and incomplete_count == 0

    async def get_leg_completion_status(
        self,
        event_id: int,
        leg_number: int,
        phase: Optional[str] = None,
    ) -> dict:
        """
        Get detailed leg completion status.

        Returns:
            dict with total_cards, completed_cards, is_complete, completion_percentage
        """
        # Total cards in this leg
        total_query = select(func.count(TAGameCard.id)).where(
            TAGameCard.event_id == event_id,
            TAGameCard.leg_number == leg_number,
        )
        if phase:
            total_query = total_query.where(TAGameCard.phase == phase)

        total_result = await self.db.execute(total_query)
        total_cards = total_result.scalar() or 0

        # Completed (validated) cards
        completed_query = select(func.count(TAGameCard.id)).where(
            TAGameCard.event_id == event_id,
            TAGameCard.leg_number == leg_number,
            TAGameCard.status == TAGameCardStatus.VALIDATED.value,
        )
        if phase:
            completed_query = completed_query.where(TAGameCard.phase == phase)

        completed_result = await self.db.execute(completed_query)
        completed_cards = completed_result.scalar() or 0

        is_complete = total_cards > 0 and completed_cards == total_cards
        completion_percentage = (completed_cards / total_cards * 100) if total_cards > 0 else 0

        return {
            "leg_number": leg_number,
            "phase": phase,
            "total_cards": total_cards,
            "completed_cards": completed_cards,
            "is_complete": is_complete,
            "completion_percentage": round(completion_percentage, 1),
        }

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

        Ranking is based on (8 tiebreakers - Story 12.2):
        1. Total points (desc) - primary
        2. Total fish caught (desc)
        3. Number of victories (desc)
        4. Ties with fish (desc)
        5. Ties without fish (desc)
        6. Losses with fish (asc - fewer is better)
        7. Losses without fish (asc - fewer is better)
        8. Direct match result (if still tied)
        """
        # Clear direct match cache for fresh calculation
        self._direct_match_cache.clear()

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
        standings = list(result.scalars().all())

        movements = []
        previous_leader = None

        # Get previous leader
        for s in standings:
            if s.rank == 1:
                previous_leader = s.user_id
                break

        # Helper to get standing stats tuple for comparison (first 7 criteria)
        def get_stats_tuple(s: TAQualifierStanding) -> tuple:
            return (
                s.total_points,
                s.total_fish_caught,
                s.total_victories,
                s.ties_with_fish,
                s.ties_without_fish,
                s.losses_with_fish,
                s.losses_without_fish,
            )

        # Story 12.2: Apply 8th tiebreaker (direct match) for tied standings
        # Group consecutive standings with identical 7-criteria stats
        i = 0
        while i < len(standings):
            # Find the end of the tie group
            tie_start = i
            tie_stats = get_stats_tuple(standings[i])
            while i < len(standings) and get_stats_tuple(standings[i]) == tie_stats:
                i += 1
            tie_end = i

            # If there's a tie group (more than 1 standing)
            if tie_end - tie_start > 1:
                tie_group = standings[tie_start:tie_end]

                # Resolve ties using direct match (8th tiebreaker)
                # Simple bubble sort with async direct match lookup
                for j in range(len(tie_group)):
                    for k in range(j + 1, len(tie_group)):
                        direct_result = await self._get_direct_match_result(
                            event_id,
                            tie_group[j].user_id,
                            tie_group[k].user_id,
                        )
                        # If k beat j in direct match, swap them
                        if direct_result == -1:
                            tie_group[j], tie_group[k] = tie_group[k], tie_group[j]

                # Put sorted tie group back
                standings[tie_start:tie_end] = tie_group

        # Assign new ranks
        for rank_pos, standing in enumerate(standings, 1):
            previous_rank = standing.rank
            standing.rank = rank_pos
            standing.updated_at = datetime.now(timezone.utc)

            change = (previous_rank - rank_pos) if previous_rank else rank_pos
            is_new_leader = (rank_pos == 1 and standing.user_id != previous_leader)

            user_name = (
                f"{standing.user.profile.first_name} {standing.user.profile.last_name}".strip()
                if standing.user and standing.user.profile
                else f"User {standing.user_id}"
            )

            if previous_rank != rank_pos:
                movements.append(RankingMovement(
                    user_id=standing.user_id,
                    user_name=user_name,
                    previous_rank=previous_rank,
                    current_rank=rank_pos,
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

        # Sort using tie-break rules (first 7 criteria)
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

        # Story 12.2: Apply 8th tiebreaker (direct match) for tied competitors
        def get_stats_tuple(c: dict) -> tuple:
            return (
                c["points"],
                c["captures"],
                c["victories"],
                c["ties_with_fish"],
                c["ties_without_fish"],
                c["losses_with_fish"],
                c["losses_without_fish"],
            )

        # Clear direct match cache for fresh calculation
        self._direct_match_cache.clear()

        # Track direct match results between consecutive competitors for rank sharing
        direct_match_results: dict[tuple[int, int], int] = {}

        # Group consecutive competitors with identical 7-criteria stats and resolve with direct match
        i = 0
        while i < len(comp_list):
            tie_start = i
            tie_stats = get_stats_tuple(comp_list[i])
            while i < len(comp_list) and get_stats_tuple(comp_list[i]) == tie_stats:
                i += 1
            tie_end = i

            # If there's a tie group (more than 1 competitor)
            if tie_end - tie_start > 1:
                tie_group = comp_list[tie_start:tie_end]

                # Resolve ties using direct match (8th tiebreaker)
                for j in range(len(tie_group)):
                    for k in range(j + 1, len(tie_group)):
                        direct_result = await self._get_direct_match_result(
                            event_id,
                            tie_group[j]["user_id"],
                            tie_group[k]["user_id"],
                            phase,
                        )
                        # Store result for rank sharing decision
                        direct_match_results[(tie_group[j]["user_id"], tie_group[k]["user_id"])] = direct_result
                        direct_match_results[(tie_group[k]["user_id"], tie_group[j]["user_id"])] = -direct_result
                        # If k beat j in direct match, swap them
                        if direct_result == -1:
                            tie_group[j], tie_group[k] = tie_group[k], tie_group[j]

                # Put sorted tie group back
                comp_list[tie_start:tie_end] = tie_group

        # Assign ranks with 8th tiebreaker awareness
        # Only share ranks if stats are identical AND direct match didn't resolve
        self._assign_ranks_with_direct_match(comp_list, direct_match_results)

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

    def _assign_ranks_with_direct_match(
        self,
        comp_list: list[dict],
        direct_match_results: dict[tuple[int, int], int],
    ) -> None:
        """
        Assign ranks considering direct match results (Story 12.2: 8th tiebreaker).

        Competitors share a rank ONLY if:
        1. Their 7-criteria stats are identical, AND
        2. Their direct match result is 0 (no direct match or tied record)

        If direct match was decisive (non-zero), they get sequential ranks.
        """
        if not comp_list:
            return

        last_rank = 1
        last_stats = None
        last_user_id = None

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
            current_user_id = comp["user_id"]

            if i == 0:
                comp["rank"] = 1
                last_stats = current_stats
                last_user_id = current_user_id
                continue

            # Check if should share rank:
            # 1. Stats must be identical
            # 2. Direct match between them must be 0 (undecided)
            if current_stats == last_stats:
                # Check direct match result between this and previous competitor
                direct_result = direct_match_results.get((last_user_id, current_user_id), 0)
                if direct_result == 0:
                    # Still tied after 8th tiebreaker - share rank
                    comp["rank"] = last_rank
                else:
                    # Direct match resolved the tie - sequential rank
                    comp["rank"] = i + 1
                    last_rank = i + 1
            else:
                # Different stats - sequential rank
                comp["rank"] = i + 1
                last_rank = i + 1

            last_stats = current_stats
            last_user_id = current_user_id

    async def get_leg_matches(
        self,
        event_id: int,
        leg_number: int,
        phase: Optional[str] = None,
    ) -> list[dict]:
        """Get all matches for a specific leg with A vs B details.

        Note: leg_number parameter is the display/round number (1, 2, 3... within each phase),
        not the sequential leg_number field which continues across phases.
        This matches the schedule endpoint behavior where round_number is returned as leg_number.
        """
        query = (
            select(TAMatch)
            .where(
                TAMatch.event_id == event_id,
                TAMatch.round_number == leg_number,  # Use round_number for display consistency
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
                "leg_number": m.round_number,  # Use round_number as display leg_number for consistency
                "round_number": m.round_number,
                "match_number": m.match_number,
                "phase": m.phase,
                "seat_a": m.seat_a,
                "seat_b": m.seat_b,
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

        # NEW: Catches per participant
        catches_per_participant = total_catches / total_participants if total_participants > 0 else 0.0

        # NEW: Catches per match per competitor (divide by 2 since each match has 2 players)
        catches_per_match_per_competitor = (total_catches / completed_matches / 2) if completed_matches > 0 else 0.0

        # NEW: Average catches per minute (need match duration)
        match_duration_minutes = settings.match_duration_minutes if settings else 15
        total_match_minutes = completed_matches * match_duration_minutes
        average_catches_per_minute = total_catches / total_match_minutes if total_match_minutes > 0 else 0.0

        # NEW: Best performance - player with highest average catches per match
        best_performance = None
        if rankings:
            # Calculate avg catches per match for each player
            for r in rankings:
                matches_played = r.get("matches_played", 0)
                if matches_played > 0:
                    r["avg_catches_per_match"] = r["captures"] / matches_played
                else:
                    r["avg_catches_per_match"] = 0.0

            perf_sorted = sorted(rankings, key=lambda x: -x.get("avg_catches_per_match", 0))
            if perf_sorted and perf_sorted[0].get("avg_catches_per_match", 0) > 0:
                best_performance = {
                    "user_id": perf_sorted[0]["user_id"],
                    "user_name": perf_sorted[0].get("user_name"),
                    "user_avatar": perf_sorted[0].get("user_avatar"),
                    "avg_catches_per_match": round(perf_sorted[0]["avg_catches_per_match"], 2),
                }

        # NEW: Best round - round with highest total catches
        best_round = None
        round_catches_query = select(
            TAMatch.round_number,
            (func.coalesce(func.sum(TAMatch.competitor_a_catches), 0) +
             func.coalesce(func.sum(TAMatch.competitor_b_catches), 0)).label("round_catches")
        ).where(
            TAMatch.event_id == event_id,
            TAMatch.status == TAMatchStatus.COMPLETED.value,
        ).group_by(TAMatch.round_number).order_by(
            (func.coalesce(func.sum(TAMatch.competitor_a_catches), 0) +
             func.coalesce(func.sum(TAMatch.competitor_b_catches), 0)).desc()
        ).limit(1)
        round_result = await self.db.execute(round_catches_query)
        best_round_row = round_result.first()
        if best_round_row and best_round_row.round_catches > 0:
            best_round = {
                "round_number": best_round_row.round_number,
                "total_catches": best_round_row.round_catches,
            }

        return {
            "event_id": event_id,
            "total_participants": total_participants,
            "total_matches": total_matches,
            "completed_matches": completed_matches,
            "total_catches": total_catches,
            "average_catches_per_match": round(average_catches_per_match, 2),
            # NEW enhanced stats
            "catches_per_participant": round(catches_per_participant, 2),
            "catches_per_match_per_competitor": round(catches_per_match_per_competitor, 2),
            "average_catches_per_minute": round(average_catches_per_minute, 2),
            "best_performance": best_performance,
            "best_round": best_round,
            # Existing stats
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
