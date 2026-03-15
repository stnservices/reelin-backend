"""Statistics service for aggregating user statistics."""

from datetime import datetime
from typing import Optional

from sqlalchemy import case, select, func, distinct, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from app.models.statistics import UserEventTypeStats
from app.models.event import Event, EventType, EventStatus
from app.models.catch import Catch, CatchStatus, EventScoreboard
from app.models.enrollment import EventEnrollment
from app.models.trout_area import TAMatch, TAMatchStatus, TAGameCard, TAGameCardStatus, TAQualifierStanding


class StatisticsService:
    """Service for computing and updating user statistics."""

    @staticmethod
    async def get_user_statistics(
        db: AsyncSession,
        user_id: int,
    ) -> dict:
        """
        Get user statistics (overall + per event type).

        Returns dict with:
        - overall: EventTypeStatsResponse-like dict
        - by_event_type: list of EventTypeStatsResponse-like dicts
        """
        # Get all stats for user
        stmt = (
            select(UserEventTypeStats)
            .where(UserEventTypeStats.user_id == user_id)
            .options(
                selectinload(UserEventTypeStats.event_type),
                selectinload(UserEventTypeStats.largest_catch_species),
            )
        )
        result = await db.execute(stmt)
        stats_list = result.scalars().all()

        # Separate overall from per-type
        overall = None
        by_event_type = []

        for stats in stats_list:
            if stats.event_type_id is None:
                overall = stats
            else:
                by_event_type.append(stats)

        # If no overall stats exist, create empty one
        if overall is None:
            overall = await StatisticsService._create_empty_stats(db, user_id, None)

        return {
            "overall": overall,
            "by_event_type": by_event_type,
        }

    @staticmethod
    async def _create_empty_stats(
        db: AsyncSession,
        user_id: int,
        event_type_id: Optional[int],
    ) -> UserEventTypeStats:
        """Create empty stats record."""
        stats = UserEventTypeStats(
            user_id=user_id,
            event_type_id=event_type_id,
        )
        db.add(stats)
        await db.flush()
        return stats

    @staticmethod
    async def update_user_stats_for_event(
        db: AsyncSession,
        user_id: int,
        event_id: int,
    ) -> None:
        """
        Update user statistics after event participation changes.
        Called when event completes or when catches are validated.
        """
        # Get event info
        event = await db.get(Event, event_id)
        if not event:
            return

        # Update overall stats
        await StatisticsService._recalculate_stats(db, user_id, None)

        # Update event-type-specific stats
        await StatisticsService._recalculate_stats(db, user_id, event.event_type_id)

    @staticmethod
    async def recalculate_all_stats(
        db: AsyncSession,
        user_id: int,
    ) -> None:
        """
        Recalculate all statistics for a user from scratch.
        Use for data corrections or migration.
        """
        # Get all event types the user has participated in
        stmt = (
            select(distinct(Event.event_type_id))
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .where(EventEnrollment.user_id == user_id)
            .where(EventEnrollment.status == "approved")
        )
        result = await db.execute(stmt)
        event_type_ids = [row[0] for row in result.fetchall()]

        # Recalculate overall
        await StatisticsService._recalculate_stats(db, user_id, None)

        # Recalculate per event type
        for event_type_id in event_type_ids:
            await StatisticsService._recalculate_stats(db, user_id, event_type_id)

    @staticmethod
    async def _recalculate_stats(
        db: AsyncSession,
        user_id: int,
        event_type_id: Optional[int],
    ) -> UserEventTypeStats:
        """
        Recalculate statistics for a user (overall or per event type).
        """
        # Get or create stats record
        stmt = select(UserEventTypeStats).where(
            and_(
                UserEventTypeStats.user_id == user_id,
                UserEventTypeStats.event_type_id == event_type_id
                if event_type_id is not None
                else UserEventTypeStats.event_type_id.is_(None),
            )
        )
        result = await db.execute(stmt)
        stats = result.scalar_one_or_none()

        if stats is None:
            stats = UserEventTypeStats(user_id=user_id, event_type_id=event_type_id)
            db.add(stats)

        # Build event filter
        event_filter = [
            EventEnrollment.user_id == user_id,
            EventEnrollment.status == "approved",
            Event.is_test == False,  # Exclude test events from stats
        ]
        if event_type_id is not None:
            event_filter.append(Event.event_type_id == event_type_id)

        # === Count events ===
        events_stmt = (
            select(func.count(distinct(Event.id)))
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .where(and_(*event_filter))
            .where(Event.status.in_([EventStatus.ONGOING.value, EventStatus.COMPLETED.value]))
        )
        result = await db.execute(events_stmt)
        stats.total_events = result.scalar() or 0

        # Events this year
        current_year = datetime.now().year
        events_year_stmt = (
            select(func.count(distinct(Event.id)))
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .where(and_(*event_filter))
            .where(Event.status.in_([EventStatus.ONGOING.value, EventStatus.COMPLETED.value]))
            .where(func.extract("year", Event.start_date) == current_year)
        )
        result = await db.execute(events_year_stmt)
        stats.total_events_this_year = result.scalar() or 0

        # === Catch statistics ===
        catch_filter = [
            Catch.user_id == user_id,
            Event.is_test == False,  # Exclude test events
        ]
        if event_type_id is not None:
            catch_filter.append(Event.event_type_id == event_type_id)

        # Total catches
        catches_stmt = (
            select(func.count(Catch.id))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*catch_filter))
        )
        result = await db.execute(catches_stmt)
        stats.total_catches = result.scalar() or 0

        # Approved catches
        approved_stmt = (
            select(func.count(Catch.id))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*catch_filter))
            .where(Catch.status == CatchStatus.APPROVED.value)
        )
        result = await db.execute(approved_stmt)
        stats.total_approved_catches = result.scalar() or 0

        # Rejected catches
        rejected_stmt = (
            select(func.count(Catch.id))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*catch_filter))
            .where(Catch.status == CatchStatus.REJECTED.value)
        )
        result = await db.execute(rejected_stmt)
        stats.total_rejected_catches = result.scalar() or 0

        # Largest catch
        largest_stmt = (
            select(Catch.length, Catch.fish_id)
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*catch_filter))
            .where(Catch.status == CatchStatus.APPROVED.value)
            .order_by(Catch.length.desc())
            .limit(1)
        )
        result = await db.execute(largest_stmt)
        largest = result.first()
        if largest:
            stats.largest_catch_cm = largest[0]
            stats.largest_catch_species_id = largest[1]
        else:
            stats.largest_catch_cm = None
            stats.largest_catch_species_id = None

        # Average catch length
        avg_stmt = (
            select(func.avg(Catch.length))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*catch_filter))
            .where(Catch.status == CatchStatus.APPROVED.value)
        )
        result = await db.execute(avg_stmt)
        stats.average_catch_length = result.scalar() or 0.0

        # Unique species count
        species_stmt = (
            select(func.count(distinct(Catch.fish_id)))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*catch_filter))
            .where(Catch.status == CatchStatus.APPROVED.value)
        )
        result = await db.execute(species_stmt)
        stats.unique_species_count = result.scalar() or 0

        # === Scoreboard statistics ===
        scoreboard_filter = [
            EventScoreboard.user_id == user_id,
            Event.is_test == False,  # Exclude test events
        ]
        if event_type_id is not None:
            scoreboard_filter.append(Event.event_type_id == event_type_id)

        # Total points
        points_stmt = (
            select(func.sum(EventScoreboard.total_points))
            .join(Event, EventScoreboard.event_id == Event.id)
            .where(and_(*scoreboard_filter))
            .where(Event.status == EventStatus.COMPLETED.value)
        )
        result = await db.execute(points_stmt)
        stats.total_points = result.scalar() or 0.0

        # Bonus points
        bonus_stmt = (
            select(func.sum(EventScoreboard.bonus_points))
            .join(Event, EventScoreboard.event_id == Event.id)
            .where(and_(*scoreboard_filter))
            .where(Event.status == EventStatus.COMPLETED.value)
        )
        result = await db.execute(bonus_stmt)
        stats.total_bonus_points = result.scalar() or 0

        # Penalty points
        penalty_stmt = (
            select(func.sum(EventScoreboard.penalty_points))
            .join(Event, EventScoreboard.event_id == Event.id)
            .where(and_(*scoreboard_filter))
            .where(Event.status == EventStatus.COMPLETED.value)
        )
        result = await db.execute(penalty_stmt)
        stats.total_penalty_points = result.scalar() or 0

        # Wins (1st place)
        wins_stmt = (
            select(func.count(EventScoreboard.id))
            .join(Event, EventScoreboard.event_id == Event.id)
            .where(and_(*scoreboard_filter))
            .where(Event.status == EventStatus.COMPLETED.value)
            .where(EventScoreboard.rank == 1)
        )
        result = await db.execute(wins_stmt)
        stats.total_wins = result.scalar() or 0

        # Podium finishes (top 3)
        podium_stmt = (
            select(func.count(EventScoreboard.id))
            .join(Event, EventScoreboard.event_id == Event.id)
            .where(and_(*scoreboard_filter))
            .where(Event.status == EventStatus.COMPLETED.value)
            .where(EventScoreboard.rank <= 3)
        )
        result = await db.execute(podium_stmt)
        stats.podium_finishes = result.scalar() or 0

        # Best rank
        best_rank_stmt = (
            select(func.min(EventScoreboard.rank))
            .join(Event, EventScoreboard.event_id == Event.id)
            .where(and_(*scoreboard_filter))
            .where(Event.status == EventStatus.COMPLETED.value)
            .where(EventScoreboard.rank > 0)
        )
        result = await db.execute(best_rank_stmt)
        stats.best_rank = result.scalar()

        # Last event
        last_event_stmt = (
            select(Event.id, Event.end_date)
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .where(and_(*event_filter))
            .where(Event.status.in_([EventStatus.ONGOING.value, EventStatus.COMPLETED.value]))
            .order_by(Event.end_date.desc())
            .limit(1)
        )
        result = await db.execute(last_event_stmt)
        last_event = result.first()
        if last_event:
            stats.last_event_id = last_event[0]
            stats.last_event_date = last_event[1]

        # === TA Statistics ===
        # Only calculate for overall stats (event_type_id is None)
        # TA stats are global across all events, not per event type
        if event_type_id is None:
            ta_stats = await StatisticsService._calc_ta_stats(db, user_id)
            # Only update if user has any TA participation
            if any(v is not None for v in ta_stats.values()):
                stats.ta_total_matches = ta_stats.get('ta_total_matches')
                stats.ta_match_wins = ta_stats.get('ta_match_wins')
                stats.ta_match_losses = ta_stats.get('ta_match_losses')
                stats.ta_match_ties = ta_stats.get('ta_match_ties')
                stats.ta_total_catches = ta_stats.get('ta_total_catches')
                stats.ta_tournament_wins = ta_stats.get('ta_tournament_wins')
                stats.ta_tournament_podiums = ta_stats.get('ta_tournament_podiums')

        stats.last_updated = datetime.utcnow()
        await db.flush()

        return stats

    @staticmethod
    async def _calc_ta_stats(
        db: AsyncSession,
        user_id: int,
    ) -> dict:
        """
        Calculate TA-specific statistics for a user.

        Uses 3 queries (down from 11) via SQL CASE expressions.

        Returns dict with keys:
        - ta_total_matches, ta_match_wins, ta_match_losses, ta_match_ties
        - ta_total_catches, ta_tournament_wins, ta_tournament_podiums

        Returns all None values if user has no TA participation.
        """
        # Query 1: Match stats — total, wins, losses, ties (replaces 7 queries)
        is_a = TAMatch.competitor_a_id == user_id
        is_b = TAMatch.competitor_b_id == user_id
        match_result = await db.execute(
            select(
                func.count(TAMatch.id).label("total"),
                func.count(case(
                    (and_(is_a, TAMatch.competitor_a_outcome_code.like("V%")), 1),
                    (and_(is_b, TAMatch.competitor_b_outcome_code.like("V%")), 1),
                )).label("wins"),
                func.count(case(
                    (and_(is_a, TAMatch.competitor_a_outcome_code.like("L%")), 1),
                    (and_(is_b, TAMatch.competitor_b_outcome_code.like("L%")), 1),
                )).label("losses"),
                func.count(case(
                    (and_(is_a, TAMatch.competitor_a_outcome_code.like("T%")), 1),
                    (and_(is_b, TAMatch.competitor_b_outcome_code.like("T%")), 1),
                )).label("ties"),
            )
            .where(or_(is_a, is_b))
            .where(TAMatch.status == TAMatchStatus.COMPLETED.value)
        )
        row = match_result.one()
        total_matches = row.total or 0

        if total_matches == 0:
            return {
                "ta_total_matches": None,
                "ta_match_wins": None,
                "ta_match_losses": None,
                "ta_match_ties": None,
                "ta_total_catches": None,
                "ta_tournament_wins": None,
                "ta_tournament_podiums": None,
            }

        # Query 2: Catches from validated game cards in completed matches
        catches_result = await db.execute(
            select(func.coalesce(func.sum(TAGameCard.my_catches), 0))
            .join(TAMatch, TAGameCard.match_id == TAMatch.id)
            .where(TAGameCard.user_id == user_id)
            .where(TAGameCard.status == TAGameCardStatus.VALIDATED.value)
            .where(TAMatch.status == TAMatchStatus.COMPLETED.value)
            .where(TAGameCard.my_catches.isnot(None))
        )
        total_catches = catches_result.scalar() or 0

        # Query 3: Tournament wins + podiums (replaces 2 queries)
        tournament_result = await db.execute(
            select(
                func.count(case((TAQualifierStanding.rank == 1, 1))).label("wins"),
                func.count(case((TAQualifierStanding.rank <= 3, 1))).label("podiums"),
            )
            .join(Event, TAQualifierStanding.event_id == Event.id)
            .where(TAQualifierStanding.user_id == user_id)
            .where(Event.status == EventStatus.COMPLETED.value)
            .where(Event.is_test == False)
        )
        t_row = tournament_result.one()

        return {
            "ta_total_matches": total_matches,
            "ta_match_wins": row.wins or 0,
            "ta_match_losses": row.losses or 0,
            "ta_match_ties": row.ties or 0,
            "ta_total_catches": total_catches,
            "ta_tournament_wins": t_row.wins or 0,
            "ta_tournament_podiums": t_row.podiums or 0,
        }

    # ── Sync versions for Celery tasks (psycopg2) ──────────────────────

    @staticmethod
    def update_user_stats_for_event_sync(
        db: Session,
        user_id: int,
        event_id: int,
    ) -> None:
        """Sync version of update_user_stats_for_event for Celery tasks."""
        event = db.get(Event, event_id)
        if not event:
            return
        StatisticsService._recalculate_stats_sync(db, user_id, None)
        StatisticsService._recalculate_stats_sync(db, user_id, event.event_type_id)

    @staticmethod
    def recalculate_all_stats_sync(
        db: Session,
        user_id: int,
    ) -> None:
        """Sync version of recalculate_all_stats for Celery tasks."""
        stmt = (
            select(distinct(Event.event_type_id))
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .where(EventEnrollment.user_id == user_id)
            .where(EventEnrollment.status == "approved")
        )
        result = db.execute(stmt)
        event_type_ids = [row[0] for row in result.fetchall()]

        StatisticsService._recalculate_stats_sync(db, user_id, None)
        for event_type_id in event_type_ids:
            StatisticsService._recalculate_stats_sync(db, user_id, event_type_id)

    @staticmethod
    def _recalculate_stats_sync(
        db: Session,
        user_id: int,
        event_type_id: Optional[int],
    ) -> UserEventTypeStats:
        """Sync version of _recalculate_stats for Celery tasks."""
        stmt = select(UserEventTypeStats).where(
            and_(
                UserEventTypeStats.user_id == user_id,
                UserEventTypeStats.event_type_id == event_type_id
                if event_type_id is not None
                else UserEventTypeStats.event_type_id.is_(None),
            )
        )
        result = db.execute(stmt)
        stats = result.scalar_one_or_none()

        if stats is None:
            stats = UserEventTypeStats(user_id=user_id, event_type_id=event_type_id)
            db.add(stats)

        event_filter = [
            EventEnrollment.user_id == user_id,
            EventEnrollment.status == "approved",
            Event.is_test == False,
        ]
        if event_type_id is not None:
            event_filter.append(Event.event_type_id == event_type_id)

        events_stmt = (
            select(func.count(distinct(Event.id)))
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .where(and_(*event_filter))
            .where(Event.status.in_([EventStatus.ONGOING.value, EventStatus.COMPLETED.value]))
        )
        result = db.execute(events_stmt)
        stats.total_events = result.scalar() or 0

        current_year = datetime.now().year
        events_year_stmt = (
            select(func.count(distinct(Event.id)))
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .where(and_(*event_filter))
            .where(Event.status.in_([EventStatus.ONGOING.value, EventStatus.COMPLETED.value]))
            .where(func.extract("year", Event.start_date) == current_year)
        )
        result = db.execute(events_year_stmt)
        stats.total_events_this_year = result.scalar() or 0

        catch_filter = [
            Catch.user_id == user_id,
            Event.is_test == False,
        ]
        if event_type_id is not None:
            catch_filter.append(Event.event_type_id == event_type_id)

        catches_stmt = (
            select(func.count(Catch.id))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*catch_filter))
        )
        result = db.execute(catches_stmt)
        stats.total_catches = result.scalar() or 0

        approved_stmt = (
            select(func.count(Catch.id))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*catch_filter))
            .where(Catch.status == CatchStatus.APPROVED.value)
        )
        result = db.execute(approved_stmt)
        stats.total_approved_catches = result.scalar() or 0

        rejected_stmt = (
            select(func.count(Catch.id))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*catch_filter))
            .where(Catch.status == CatchStatus.REJECTED.value)
        )
        result = db.execute(rejected_stmt)
        stats.total_rejected_catches = result.scalar() or 0

        largest_stmt = (
            select(Catch.length, Catch.fish_id)
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*catch_filter))
            .where(Catch.status == CatchStatus.APPROVED.value)
            .order_by(Catch.length.desc())
            .limit(1)
        )
        result = db.execute(largest_stmt)
        largest = result.first()
        if largest:
            stats.largest_catch_cm = largest[0]
            stats.largest_catch_species_id = largest[1]
        else:
            stats.largest_catch_cm = None
            stats.largest_catch_species_id = None

        avg_stmt = (
            select(func.avg(Catch.length))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*catch_filter))
            .where(Catch.status == CatchStatus.APPROVED.value)
        )
        result = db.execute(avg_stmt)
        stats.average_catch_length = result.scalar() or 0.0

        species_stmt = (
            select(func.count(distinct(Catch.fish_id)))
            .join(Event, Catch.event_id == Event.id)
            .where(and_(*catch_filter))
            .where(Catch.status == CatchStatus.APPROVED.value)
        )
        result = db.execute(species_stmt)
        stats.unique_species_count = result.scalar() or 0

        scoreboard_filter = [
            EventScoreboard.user_id == user_id,
            Event.is_test == False,
        ]
        if event_type_id is not None:
            scoreboard_filter.append(Event.event_type_id == event_type_id)

        points_stmt = (
            select(func.sum(EventScoreboard.total_points))
            .join(Event, EventScoreboard.event_id == Event.id)
            .where(and_(*scoreboard_filter))
            .where(Event.status == EventStatus.COMPLETED.value)
        )
        result = db.execute(points_stmt)
        stats.total_points = result.scalar() or 0.0

        bonus_stmt = (
            select(func.sum(EventScoreboard.bonus_points))
            .join(Event, EventScoreboard.event_id == Event.id)
            .where(and_(*scoreboard_filter))
            .where(Event.status == EventStatus.COMPLETED.value)
        )
        result = db.execute(bonus_stmt)
        stats.total_bonus_points = result.scalar() or 0

        penalty_stmt = (
            select(func.sum(EventScoreboard.penalty_points))
            .join(Event, EventScoreboard.event_id == Event.id)
            .where(and_(*scoreboard_filter))
            .where(Event.status == EventStatus.COMPLETED.value)
        )
        result = db.execute(penalty_stmt)
        stats.total_penalty_points = result.scalar() or 0

        wins_stmt = (
            select(func.count(EventScoreboard.id))
            .join(Event, EventScoreboard.event_id == Event.id)
            .where(and_(*scoreboard_filter))
            .where(Event.status == EventStatus.COMPLETED.value)
            .where(EventScoreboard.rank == 1)
        )
        result = db.execute(wins_stmt)
        stats.total_wins = result.scalar() or 0

        podium_stmt = (
            select(func.count(EventScoreboard.id))
            .join(Event, EventScoreboard.event_id == Event.id)
            .where(and_(*scoreboard_filter))
            .where(Event.status == EventStatus.COMPLETED.value)
            .where(EventScoreboard.rank <= 3)
        )
        result = db.execute(podium_stmt)
        stats.podium_finishes = result.scalar() or 0

        best_rank_stmt = (
            select(func.min(EventScoreboard.rank))
            .join(Event, EventScoreboard.event_id == Event.id)
            .where(and_(*scoreboard_filter))
            .where(Event.status == EventStatus.COMPLETED.value)
            .where(EventScoreboard.rank > 0)
        )
        result = db.execute(best_rank_stmt)
        stats.best_rank = result.scalar()

        last_event_stmt = (
            select(Event.id, Event.end_date)
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .where(and_(*event_filter))
            .where(Event.status.in_([EventStatus.ONGOING.value, EventStatus.COMPLETED.value]))
            .order_by(Event.end_date.desc())
            .limit(1)
        )
        result = db.execute(last_event_stmt)
        last_event = result.first()
        if last_event:
            stats.last_event_id = last_event[0]
            stats.last_event_date = last_event[1]

        if event_type_id is None:
            ta_stats = StatisticsService._calc_ta_stats_sync(db, user_id)
            if any(v is not None for v in ta_stats.values()):
                stats.ta_total_matches = ta_stats.get('ta_total_matches')
                stats.ta_match_wins = ta_stats.get('ta_match_wins')
                stats.ta_match_losses = ta_stats.get('ta_match_losses')
                stats.ta_match_ties = ta_stats.get('ta_match_ties')
                stats.ta_total_catches = ta_stats.get('ta_total_catches')
                stats.ta_tournament_wins = ta_stats.get('ta_tournament_wins')
                stats.ta_tournament_podiums = ta_stats.get('ta_tournament_podiums')

        stats.last_updated = datetime.utcnow()
        db.flush()

        return stats

    @staticmethod
    def _calc_ta_stats_sync(
        db: Session,
        user_id: int,
    ) -> dict:
        """Sync version of _calc_ta_stats for Celery tasks. 3 queries via CASE."""
        is_a = TAMatch.competitor_a_id == user_id
        is_b = TAMatch.competitor_b_id == user_id
        match_result = db.execute(
            select(
                func.count(TAMatch.id).label("total"),
                func.count(case(
                    (and_(is_a, TAMatch.competitor_a_outcome_code.like("V%")), 1),
                    (and_(is_b, TAMatch.competitor_b_outcome_code.like("V%")), 1),
                )).label("wins"),
                func.count(case(
                    (and_(is_a, TAMatch.competitor_a_outcome_code.like("L%")), 1),
                    (and_(is_b, TAMatch.competitor_b_outcome_code.like("L%")), 1),
                )).label("losses"),
                func.count(case(
                    (and_(is_a, TAMatch.competitor_a_outcome_code.like("T%")), 1),
                    (and_(is_b, TAMatch.competitor_b_outcome_code.like("T%")), 1),
                )).label("ties"),
            )
            .where(or_(is_a, is_b))
            .where(TAMatch.status == TAMatchStatus.COMPLETED.value)
        )
        row = match_result.one()
        total_matches = row.total or 0

        if total_matches == 0:
            return {
                "ta_total_matches": None,
                "ta_match_wins": None,
                "ta_match_losses": None,
                "ta_match_ties": None,
                "ta_total_catches": None,
                "ta_tournament_wins": None,
                "ta_tournament_podiums": None,
            }

        catches_result = db.execute(
            select(func.coalesce(func.sum(TAGameCard.my_catches), 0))
            .join(TAMatch, TAGameCard.match_id == TAMatch.id)
            .where(TAGameCard.user_id == user_id)
            .where(TAGameCard.status == TAGameCardStatus.VALIDATED.value)
            .where(TAMatch.status == TAMatchStatus.COMPLETED.value)
            .where(TAGameCard.my_catches.isnot(None))
        )
        total_catches = catches_result.scalar() or 0

        tournament_result = db.execute(
            select(
                func.count(case((TAQualifierStanding.rank == 1, 1))).label("wins"),
                func.count(case((TAQualifierStanding.rank <= 3, 1))).label("podiums"),
            )
            .join(Event, TAQualifierStanding.event_id == Event.id)
            .where(TAQualifierStanding.user_id == user_id)
            .where(Event.status == EventStatus.COMPLETED.value)
            .where(Event.is_test == False)
        )
        t_row = tournament_result.one()

        return {
            "ta_total_matches": total_matches,
            "ta_match_wins": row.wins or 0,
            "ta_match_losses": row.losses or 0,
            "ta_match_ties": row.ties or 0,
            "ta_total_catches": total_catches,
            "ta_tournament_wins": t_row.wins or 0,
            "ta_tournament_podiums": t_row.podiums or 0,
        }

# Singleton instance
statistics_service = StatisticsService()
