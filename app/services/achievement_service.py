"""Achievement service for checking and awarding achievements."""

from datetime import datetime, timedelta
from typing import Optional, List

from sqlalchemy import select, func, and_, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.achievement import (
    AchievementDefinition,
    UserAchievement,
    UserAchievementProgress,
    UserStreakTracker,
    AchievementCategory,
    AchievementType,
    AchievementTier,
)
from app.models.statistics import UserEventTypeStats
from app.models.event import Event, EventStatus
from app.models.catch import Catch, CatchStatus, EventScoreboard
from app.models.enrollment import EventEnrollment
from app.models.fish import Fish


class AchievementService:
    """Service for checking and awarding achievements."""

    # Tier thresholds for each tiered achievement type
    TIER_THRESHOLDS = {
        AchievementType.PARTICIPATION.value: {
            AchievementTier.BRONZE.value: 1,
            AchievementTier.SILVER.value: 5,
            AchievementTier.GOLD.value: 10,
            AchievementTier.PLATINUM.value: 25,
        },
        AchievementType.CATCH_COUNT.value: {
            AchievementTier.BRONZE.value: 10,
            AchievementTier.SILVER.value: 50,
            AchievementTier.GOLD.value: 100,
            AchievementTier.PLATINUM.value: 500,
        },
        AchievementType.SPECIES_COUNT.value: {
            AchievementTier.BRONZE.value: 3,
            AchievementTier.SILVER.value: 5,
            AchievementTier.GOLD.value: 10,
            AchievementTier.PLATINUM.value: 15,
        },
        AchievementType.PODIUM_COUNT.value: {
            AchievementTier.BRONZE.value: 1,
            AchievementTier.SILVER.value: 3,
            AchievementTier.GOLD.value: 5,
            AchievementTier.PLATINUM.value: 10,
        },
        AchievementType.WIN_COUNT.value: {
            AchievementTier.BRONZE.value: 1,
            AchievementTier.SILVER.value: 2,
            AchievementTier.GOLD.value: 3,
            AchievementTier.PLATINUM.value: 5,
        },
        AchievementType.FISH_CATCH_COUNT.value: {
            AchievementTier.BRONZE.value: 5,
            AchievementTier.SILVER.value: 15,
            AchievementTier.GOLD.value: 30,
            AchievementTier.PLATINUM.value: 50,
        },
        AchievementType.PREDATOR_CATCH_COUNT.value: {
            AchievementTier.BRONZE.value: 25,
            AchievementTier.SILVER.value: 75,
            AchievementTier.GOLD.value: 150,
            AchievementTier.PLATINUM.value: 300,
        },
        # TA-specific tiered achievements
        AchievementType.TA_MATCH_WINS.value: {
            AchievementTier.BRONZE.value: 10,
            AchievementTier.SILVER.value: 25,
            AchievementTier.GOLD.value: 50,
            AchievementTier.PLATINUM.value: 100,
        },
        # TSF-specific tiered achievements
        AchievementType.TSF_SECTOR_WINS.value: {
            AchievementTier.BRONZE.value: 5,
            AchievementTier.SILVER.value: 15,
            AchievementTier.GOLD.value: 30,
            AchievementTier.PLATINUM.value: 50,
        },
    }

    # All predator fish slugs (matches migration seed data)
    PREDATOR_FISH_SLUGS = [
        "pike", "zander", "perch", "asp", "volga-zander",
        "wels-catfish", "chub", "huchen", "brown-trout", "rainbow-trout",
        "brook-trout", "grayling", "ide", "burbot",
    ]

    @staticmethod
    async def get_user_achievements(
        db: AsyncSession,
        user_id: int,
    ) -> dict:
        """
        Get all achievements for a user (earned + progress).
        """
        # Get earned achievements
        earned_stmt = (
            select(UserAchievement)
            .where(UserAchievement.user_id == user_id)
            .options(
                selectinload(UserAchievement.achievement),
                selectinload(UserAchievement.event),
            )
            .order_by(UserAchievement.earned_at.desc())
        )
        result = await db.execute(earned_stmt)
        earned = result.scalars().all()

        # Get progress for tiered achievements
        progress_stmt = (
            select(UserAchievementProgress)
            .where(UserAchievementProgress.user_id == user_id)
            .where(UserAchievementProgress.event_type_id.is_(None))  # Overall progress
        )
        result = await db.execute(progress_stmt)
        progress_records = result.scalars().all()

        # Build progress response with current tier info
        progress = []
        for p in progress_records:
            if p.achievement_type in AchievementService.TIER_THRESHOLDS:
                thresholds = AchievementService.TIER_THRESHOLDS[p.achievement_type]
                current_tier = None
                next_tier = None
                next_threshold = None

                # Determine current and next tier
                for tier, threshold in thresholds.items():
                    if p.current_value >= threshold:
                        current_tier = tier
                    elif next_tier is None:
                        next_tier = tier
                        next_threshold = threshold

                # Calculate progress percentage
                progress_pct = 0
                if next_threshold:
                    current_base = thresholds.get(current_tier, 0) if current_tier else 0
                    progress_pct = min(100, ((p.current_value - current_base) / (next_threshold - current_base)) * 100)
                else:
                    progress_pct = 100  # Maxed out

                progress.append({
                    "achievement_type": p.achievement_type,
                    "current_tier": current_tier,
                    "current_value": p.current_value,
                    "next_tier": next_tier,
                    "next_threshold": next_threshold,
                    "progress_percentage": progress_pct,
                })

        # Get all available achievements count
        all_achievements_stmt = select(func.count(AchievementDefinition.id)).where(
            AchievementDefinition.is_active == True
        )
        result = await db.execute(all_achievements_stmt)
        total_available = result.scalar() or 0

        return {
            "earned_achievements": earned,
            "progress": progress,
            "total_earned": len(earned),
            "total_available": total_available,
        }

    @staticmethod
    async def get_all_achievements(db: AsyncSession) -> dict:
        """Get all available achievements (badge gallery)."""
        stmt = (
            select(AchievementDefinition)
            .where(AchievementDefinition.is_active == True)
            .options(selectinload(AchievementDefinition.event_type))
            .order_by(AchievementDefinition.sort_order)
        )
        result = await db.execute(stmt)
        achievements = result.scalars().all()

        tiered = [a for a in achievements if a.category == AchievementCategory.TIERED.value]
        special = [a for a in achievements if a.category == AchievementCategory.SPECIAL.value]

        return {
            "tiered": tiered,
            "special": special,
            "total": len(achievements),
        }

    @staticmethod
    async def check_and_award_achievements(
        db: AsyncSession,
        user_id: int,
        trigger: str,
        event_id: Optional[int] = None,
        catch_id: Optional[int] = None,
        context: Optional[dict] = None,
        format_code: Optional[str] = None,
    ) -> List[AchievementDefinition]:
        """
        Check and award any achievements based on trigger.

        Args:
            db: Database session
            user_id: User to check achievements for
            trigger: Trigger event (e.g., "catch_approved", "event_completed")
            event_id: Optional event context
            catch_id: Optional catch context
            context: Optional additional context data
            format_code: If provided, only check achievements applicable to this format.
                        Valid values: "sf", "ta", "tsf", or None (check all).

        Returns list of newly awarded achievements.
        """
        newly_awarded = []

        # Check tiered achievements
        tiered_awards = await AchievementService._check_tiered_achievements(
            db, user_id, trigger, event_id, catch_id, context, format_code
        )
        newly_awarded.extend(tiered_awards)

        # Check fish-specific achievements (species and predator)
        # Note: Fish achievements are SF-only, so filter if format specified
        if format_code is None or format_code == "sf":
            fish_awards = await AchievementService._check_fish_achievements(
                db, user_id, trigger, event_id, catch_id, context
            )
            newly_awarded.extend(fish_awards)

        # Check special achievements
        special_awards = await AchievementService._check_special_achievements(
            db, user_id, trigger, event_id, catch_id, context, format_code
        )
        newly_awarded.extend(special_awards)

        # Check cross-format achievements (triggered by any format)
        # These have applicable_formats = NULL and are checked regardless of format_code
        cross_format_awards = await AchievementService._check_cross_format_achievements(
            db, user_id, trigger, event_id, context
        )
        newly_awarded.extend(cross_format_awards)

        return newly_awarded

    @staticmethod
    async def _check_tiered_achievements(
        db: AsyncSession,
        user_id: int,
        trigger: str,
        event_id: Optional[int],
        catch_id: Optional[int],
        context: Optional[dict],
        format_code: Optional[str] = None,
    ) -> List[AchievementDefinition]:
        """Check and award tiered achievements.

        Args:
            format_code: If provided, only check achievements applicable to this format.
        """
        newly_awarded = []

        # Get user's overall stats
        stats_stmt = (
            select(UserEventTypeStats)
            .where(UserEventTypeStats.user_id == user_id)
            .where(UserEventTypeStats.event_type_id.is_(None))
        )
        result = await db.execute(stats_stmt)
        stats = result.scalar_one_or_none()

        if not stats:
            return newly_awarded

        # Map achievement types to stat values
        type_to_value = {
            AchievementType.PARTICIPATION.value: stats.total_events,
            AchievementType.CATCH_COUNT.value: stats.total_approved_catches,
            AchievementType.SPECIES_COUNT.value: stats.unique_species_count,
            AchievementType.PODIUM_COUNT.value: stats.podium_finishes,
            AchievementType.WIN_COUNT.value: stats.total_wins,
        }

        # Check each tiered achievement type
        for achievement_type, current_value in type_to_value.items():
            # Update progress
            progress = await AchievementService._get_or_create_progress(
                db, user_id, achievement_type, None
            )
            progress.current_value = current_value
            progress.last_updated = datetime.utcnow()

            # Check thresholds
            thresholds = AchievementService.TIER_THRESHOLDS.get(achievement_type, {})
            for tier, threshold in thresholds.items():
                if current_value >= threshold:
                    # Try to award this tier (with format filtering)
                    code = f"{achievement_type}_{tier}"
                    awarded = await AchievementService._award_achievement_with_format(
                        db, user_id, code, event_id, catch_id, format_code
                    )
                    if awarded:
                        newly_awarded.append(awarded)

        await db.flush()
        return newly_awarded

    @staticmethod
    async def _check_fish_achievements(
        db: AsyncSession,
        user_id: int,
        trigger: str,
        event_id: Optional[int],
        catch_id: Optional[int],
        context: Optional[dict],
    ) -> List[AchievementDefinition]:
        """Check and award fish-specific achievements (species and predator)."""
        newly_awarded = []

        # Only check on catch approval
        if trigger != "catch_approved":
            return newly_awarded

        fish_id = context.get("fish_id") if context else None
        fish_slug = context.get("fish_slug") if context else None

        if not fish_id:
            return newly_awarded

        # Check species-specific achievements (Pike Master, Zander Master, etc.)
        if fish_slug in AchievementService.PREDATOR_FISH_SLUGS:
            # Count total catches of this species by user
            species_count_stmt = (
                select(func.count(Catch.id))
                .where(Catch.user_id == user_id)
                .where(Catch.fish_id == fish_id)
                .where(Catch.status == CatchStatus.APPROVED.value)
            )
            result = await db.execute(species_count_stmt)
            species_catch_count = result.scalar() or 0

            # Check species achievements
            thresholds = AchievementService.TIER_THRESHOLDS[AchievementType.FISH_CATCH_COUNT.value]
            for tier, threshold in thresholds.items():
                if species_catch_count >= threshold:
                    # Code format: fish_{slug}_{tier} (e.g., fish_pike_bronze)
                    code = f"fish_{fish_slug.replace('-', '_')}_{tier}"
                    awarded = await AchievementService._award_achievement(
                        db, user_id, code, event_id, catch_id
                    )
                    if awarded:
                        newly_awarded.append(awarded)

        # Check overall predator achievements
        if fish_slug in AchievementService.PREDATOR_FISH_SLUGS:
            # Get all predator fish IDs
            predator_stmt = (
                select(Fish.id)
                .where(Fish.slug.in_(AchievementService.PREDATOR_FISH_SLUGS))
            )
            result = await db.execute(predator_stmt)
            predator_fish_ids = [row[0] for row in result.fetchall()]

            # Count total predator catches
            predator_count_stmt = (
                select(func.count(Catch.id))
                .where(Catch.user_id == user_id)
                .where(Catch.fish_id.in_(predator_fish_ids))
                .where(Catch.status == CatchStatus.APPROVED.value)
            )
            result = await db.execute(predator_count_stmt)
            predator_catch_count = result.scalar() or 0

            # Check predator category achievements
            thresholds = AchievementService.TIER_THRESHOLDS[AchievementType.PREDATOR_CATCH_COUNT.value]
            for tier, threshold in thresholds.items():
                if predator_catch_count >= threshold:
                    code = f"predator_{tier}"
                    awarded = await AchievementService._award_achievement(
                        db, user_id, code, event_id, catch_id
                    )
                    if awarded:
                        newly_awarded.append(awarded)

        await db.flush()
        return newly_awarded

    @staticmethod
    async def _check_special_achievements(
        db: AsyncSession,
        user_id: int,
        trigger: str,
        event_id: Optional[int],
        catch_id: Optional[int],
        context: Optional[dict],
        format_code: Optional[str] = None,
    ) -> List[AchievementDefinition]:
        """Check and award special achievements based on trigger.

        Args:
            format_code: If provided, only check achievements applicable to this format.
        """
        newly_awarded = []
        context = context or {}

        # First Blood - first ever validated catch
        if trigger == "catch_approved":
            awarded = await AchievementService._check_first_blood(
                db, user_id, event_id, catch_id, format_code
            )
            if awarded:
                newly_awarded.append(awarded)

            # Trophy Hunter - catch >= 50cm
            if context.get("catch_length", 0) >= 50:
                awarded = await AchievementService._award_achievement_with_format(
                    db, user_id, "trophy_hunter", event_id, catch_id, format_code
                )
                if awarded:
                    newly_awarded.append(awarded)

            # Monster Catch - new personal best
            if context.get("is_personal_best"):
                awarded = await AchievementService._award_achievement_with_format(
                    db, user_id, "monster_catch", event_id, catch_id, format_code
                )
                if awarded:
                    newly_awarded.append(awarded)

            # Early Bird - first catch within 30 min of event start
            if context.get("is_early_bird"):
                awarded = await AchievementService._award_achievement_with_format(
                    db, user_id, "early_bird", event_id, catch_id, format_code
                )
                if awarded:
                    newly_awarded.append(awarded)

            # Last Minute Hero - catch in final 30 minutes
            if context.get("is_last_minute"):
                awarded = await AchievementService._award_achievement_with_format(
                    db, user_id, "last_minute", event_id, catch_id, format_code
                )
                if awarded:
                    newly_awarded.append(awarded)

            # Speed Demon - 5 catches in first hour
            if event_id:
                speed_awarded = await AchievementService._check_speed_demon(
                    db, user_id, event_id, format_code
                )
                if speed_awarded:
                    newly_awarded.append(speed_awarded)

        # Event completion achievements
        if trigger == "event_completed":
            # Clean Sheet - no rejected catches
            clean_awarded = await AchievementService._check_clean_sheet(
                db, user_id, event_id, format_code
            )
            if clean_awarded:
                newly_awarded.append(clean_awarded)

            # Precision Angler - 90%+ above min length
            precision_awarded = await AchievementService._check_precision_angler(
                db, user_id, event_id, format_code
            )
            if precision_awarded:
                newly_awarded.append(precision_awarded)

            # Diversity Master - all species caught
            diversity_awarded = await AchievementService._check_diversity_master(
                db, user_id, event_id, format_code
            )
            if diversity_awarded:
                newly_awarded.append(diversity_awarded)

            # Comeback King - improved 5+ ranks
            if context.get("rank_improvement", 0) >= 5:
                awarded = await AchievementService._award_achievement_with_format(
                    db, user_id, "comeback_king", event_id, None, format_code
                )
                if awarded:
                    newly_awarded.append(awarded)

            # Update streaks and check streak achievements
            streak_awards = await AchievementService._update_streaks(
                db, user_id, event_id, context, format_code
            )
            newly_awarded.extend(streak_awards)

        await db.flush()
        return newly_awarded

    @staticmethod
    async def _check_first_blood(
        db: AsyncSession,
        user_id: int,
        event_id: Optional[int],
        catch_id: Optional[int],
        format_code: Optional[str] = None,
    ) -> Optional[AchievementDefinition]:
        """Check and award First Blood (first ever catch)."""
        # Count total approved catches
        count_stmt = (
            select(func.count(Catch.id))
            .where(Catch.user_id == user_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
        )
        result = await db.execute(count_stmt)
        total_catches = result.scalar() or 0

        # Award if this is their first approved catch
        if total_catches == 1:
            return await AchievementService._award_achievement_with_format(
                db, user_id, "first_blood", event_id, catch_id, format_code
            )
        return None

    @staticmethod
    async def _check_speed_demon(
        db: AsyncSession,
        user_id: int,
        event_id: int,
        format_code: Optional[str] = None,
    ) -> Optional[AchievementDefinition]:
        """Check and award Speed Demon (5 catches in first hour)."""
        event = await db.get(Event, event_id)
        if not event:
            return None

        first_hour_end = event.start_date + timedelta(hours=1)

        # Count catches in first hour
        count_stmt = (
            select(func.count(Catch.id))
            .where(Catch.user_id == user_id)
            .where(Catch.event_id == event_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
            .where(Catch.submitted_at <= first_hour_end)
        )
        result = await db.execute(count_stmt)
        first_hour_catches = result.scalar() or 0

        if first_hour_catches >= 5:
            return await AchievementService._award_achievement_with_format(
                db, user_id, "speed_demon", event_id, None, format_code
            )
        return None

    @staticmethod
    async def _check_clean_sheet(
        db: AsyncSession,
        user_id: int,
        event_id: int,
        format_code: Optional[str] = None,
    ) -> Optional[AchievementDefinition]:
        """Check and award Clean Sheet (no rejected catches, min 3 catches)."""
        # Get catch counts
        approved_stmt = (
            select(func.count(Catch.id))
            .where(Catch.user_id == user_id)
            .where(Catch.event_id == event_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
        )
        result = await db.execute(approved_stmt)
        approved_count = result.scalar() or 0

        rejected_stmt = (
            select(func.count(Catch.id))
            .where(Catch.user_id == user_id)
            .where(Catch.event_id == event_id)
            .where(Catch.status == CatchStatus.REJECTED.value)
        )
        result = await db.execute(rejected_stmt)
        rejected_count = result.scalar() or 0

        if approved_count >= 3 and rejected_count == 0:
            return await AchievementService._award_achievement_with_format(
                db, user_id, "clean_sheet", event_id, None, format_code
            )
        return None

    @staticmethod
    async def _check_precision_angler(
        db: AsyncSession,
        user_id: int,
        event_id: int,
        format_code: Optional[str] = None,
    ) -> Optional[AchievementDefinition]:
        """Check and award Precision Angler (90%+ above min length)."""
        # This would need to check against EventFishScoring min lengths
        # For now, simplified: 90% of catches are approved (min 5)
        approved_stmt = (
            select(func.count(Catch.id))
            .where(Catch.user_id == user_id)
            .where(Catch.event_id == event_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
        )
        result = await db.execute(approved_stmt)
        approved_count = result.scalar() or 0

        total_stmt = (
            select(func.count(Catch.id))
            .where(Catch.user_id == user_id)
            .where(Catch.event_id == event_id)
        )
        result = await db.execute(total_stmt)
        total_count = result.scalar() or 0

        if total_count >= 5 and approved_count / total_count >= 0.9:
            return await AchievementService._award_achievement_with_format(
                db, user_id, "precision_angler", event_id, None, format_code
            )
        return None

    @staticmethod
    async def _check_diversity_master(
        db: AsyncSession,
        user_id: int,
        event_id: int,
        format_code: Optional[str] = None,
    ) -> Optional[AchievementDefinition]:
        """Check and award Diversity Master (caught all species in event)."""
        # Get available species for event
        from app.models.event import EventFishScoring

        available_stmt = (
            select(func.count(distinct(EventFishScoring.fish_id)))
            .where(EventFishScoring.event_id == event_id)
        )
        result = await db.execute(available_stmt)
        available_species = result.scalar() or 0

        if available_species == 0:
            return None

        # Get species caught by user
        caught_stmt = (
            select(func.count(distinct(Catch.fish_id)))
            .where(Catch.user_id == user_id)
            .where(Catch.event_id == event_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
        )
        result = await db.execute(caught_stmt)
        caught_species = result.scalar() or 0

        if caught_species >= available_species:
            return await AchievementService._award_achievement_with_format(
                db, user_id, "diversity_master", event_id, None, format_code
            )
        return None

    @staticmethod
    async def _update_streaks(
        db: AsyncSession,
        user_id: int,
        event_id: int,
        context: Optional[dict],
        format_code: Optional[str] = None,
    ) -> List[AchievementDefinition]:
        """Update streaks and check for streak achievements."""
        newly_awarded = []
        context = context or {}

        rank = context.get("final_rank")
        if rank is None:
            return newly_awarded

        # Update participation streak
        await AchievementService._update_streak(db, user_id, "participation", event_id, True)

        # Update podium streak (top 3)
        is_podium = rank <= 3
        podium_tracker = await AchievementService._update_streak(
            db, user_id, "podium", event_id, is_podium
        )
        if podium_tracker and podium_tracker.current_streak >= 3:
            awarded = await AchievementService._award_achievement_with_format(
                db, user_id, "hot_streak", event_id, None, format_code
            )
            if awarded:
                newly_awarded.append(awarded)

        # Update win streak
        is_win = rank == 1
        win_tracker = await AchievementService._update_streak(
            db, user_id, "win", event_id, is_win
        )
        if win_tracker and win_tracker.current_streak >= 2:
            awarded = await AchievementService._award_achievement_with_format(
                db, user_id, "dominator", event_id, None, format_code
            )
            if awarded:
                newly_awarded.append(awarded)

        # Check Iron Man (5 consecutive participations)
        participation_tracker = await AchievementService._get_streak_tracker(
            db, user_id, "participation"
        )
        if participation_tracker and participation_tracker.current_streak >= 5:
            awarded = await AchievementService._award_achievement_with_format(
                db, user_id, "iron_man", event_id, None, format_code
            )
            if awarded:
                newly_awarded.append(awarded)

        return newly_awarded

    @staticmethod
    async def _update_streak(
        db: AsyncSession,
        user_id: int,
        streak_type: str,
        event_id: int,
        increment: bool,
    ) -> UserStreakTracker:
        """Update a streak tracker."""
        tracker = await AchievementService._get_or_create_streak_tracker(
            db, user_id, streak_type
        )

        if increment:
            tracker.current_streak += 1
            if tracker.current_streak > tracker.max_streak:
                tracker.max_streak = tracker.current_streak
        else:
            tracker.current_streak = 0

        tracker.last_event_id = event_id
        tracker.last_updated = datetime.utcnow()

        await db.flush()
        return tracker

    @staticmethod
    async def _get_streak_tracker(
        db: AsyncSession,
        user_id: int,
        streak_type: str,
    ) -> Optional[UserStreakTracker]:
        """Get streak tracker."""
        stmt = select(UserStreakTracker).where(
            and_(
                UserStreakTracker.user_id == user_id,
                UserStreakTracker.streak_type == streak_type,
            )
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def _get_or_create_streak_tracker(
        db: AsyncSession,
        user_id: int,
        streak_type: str,
    ) -> UserStreakTracker:
        """Get or create streak tracker."""
        tracker = await AchievementService._get_streak_tracker(db, user_id, streak_type)
        if tracker is None:
            tracker = UserStreakTracker(user_id=user_id, streak_type=streak_type)
            db.add(tracker)
            await db.flush()
        return tracker

    @staticmethod
    async def _get_or_create_progress(
        db: AsyncSession,
        user_id: int,
        achievement_type: str,
        event_type_id: Optional[int],
    ) -> UserAchievementProgress:
        """Get or create progress record."""
        stmt = select(UserAchievementProgress).where(
            and_(
                UserAchievementProgress.user_id == user_id,
                UserAchievementProgress.achievement_type == achievement_type,
                UserAchievementProgress.event_type_id == event_type_id
                if event_type_id is not None
                else UserAchievementProgress.event_type_id.is_(None),
            )
        )
        result = await db.execute(stmt)
        progress = result.scalar_one_or_none()

        if progress is None:
            progress = UserAchievementProgress(
                user_id=user_id,
                achievement_type=achievement_type,
                event_type_id=event_type_id,
            )
            db.add(progress)
            await db.flush()

        return progress

    @staticmethod
    async def _award_achievement(
        db: AsyncSession,
        user_id: int,
        achievement_code: str,
        event_id: Optional[int],
        catch_id: Optional[int],
    ) -> Optional[AchievementDefinition]:
        """Award an achievement if not already earned."""
        # Get achievement definition
        achievement_stmt = select(AchievementDefinition).where(
            AchievementDefinition.code == achievement_code
        )
        result = await db.execute(achievement_stmt)
        achievement = result.scalar_one_or_none()

        if not achievement or not achievement.is_active:
            return None

        # Check if already earned
        existing_stmt = select(UserAchievement).where(
            and_(
                UserAchievement.user_id == user_id,
                UserAchievement.achievement_id == achievement.id,
            )
        )
        result = await db.execute(existing_stmt)
        existing = result.scalar_one_or_none()

        if existing:
            return None  # Already earned

        # Award achievement
        user_achievement = UserAchievement(
            user_id=user_id,
            achievement_id=achievement.id,
            event_id=event_id,
            catch_id=catch_id,
        )
        db.add(user_achievement)
        await db.flush()

        return achievement

    @staticmethod
    async def _award_achievement_with_format(
        db: AsyncSession,
        user_id: int,
        achievement_code: str,
        event_id: Optional[int],
        catch_id: Optional[int],
        format_code: Optional[str] = None,
    ) -> Optional[AchievementDefinition]:
        """
        Award an achievement if not already earned and format matches.

        This method adds format filtering to the standard _award_achievement.
        If format_code is provided, the achievement's applicable_formats will
        be checked before awarding.

        Args:
            db: Database session
            user_id: User to award achievement to
            achievement_code: Code of the achievement to award
            event_id: Optional event context
            catch_id: Optional catch context
            format_code: If provided, only award if achievement applies to this format

        Returns:
            The awarded AchievementDefinition if newly awarded, None otherwise
        """
        # Get achievement definition
        achievement_stmt = select(AchievementDefinition).where(
            AchievementDefinition.code == achievement_code
        )
        result = await db.execute(achievement_stmt)
        achievement = result.scalar_one_or_none()

        if not achievement or not achievement.is_active:
            return None

        # Check format filtering
        if format_code is not None:
            if not achievement.applies_to_format(format_code):
                return None  # Achievement doesn't apply to this format

        # Check if already earned
        existing_stmt = select(UserAchievement).where(
            and_(
                UserAchievement.user_id == user_id,
                UserAchievement.achievement_id == achievement.id,
            )
        )
        result = await db.execute(existing_stmt)
        existing = result.scalar_one_or_none()

        if existing:
            return None  # Already earned

        # Award achievement
        user_achievement = UserAchievement(
            user_id=user_id,
            achievement_id=achievement.id,
            event_id=event_id,
            catch_id=catch_id,
        )
        db.add(user_achievement)
        await db.flush()

        return achievement

    @staticmethod
    async def _check_cross_format_achievements(
        db: AsyncSession,
        user_id: int,
        trigger: str,
        event_id: Optional[int],
        context: Optional[dict],
    ) -> List[AchievementDefinition]:
        """Check and award cross-format achievements.

        These achievements require participation/wins/podiums across all 3 formats
        (SF, TA, TSF). They have applicable_formats = NULL and are checked
        regardless of the format_code.
        """
        newly_awarded = []

        # Only check on event completion
        if trigger != "event_completed":
            return newly_awarded

        # Get user's overall stats
        stats_stmt = (
            select(UserEventTypeStats)
            .where(UserEventTypeStats.user_id == user_id)
            .where(UserEventTypeStats.event_type_id.is_(None))
        )
        result = await db.execute(stats_stmt)
        stats = result.scalar_one_or_none()

        if not stats:
            return newly_awarded

        # Check format participation
        has_sf = stats.total_events > 0
        has_ta = stats.ta_total_matches is not None and stats.ta_total_matches > 0
        has_tsf = stats.tsf_total_days is not None and stats.tsf_total_days > 0

        # Format Explorer: Participated in all 3 formats
        if has_sf and has_ta and has_tsf:
            awarded = await AchievementService._award_achievement(
                db, user_id, "format_explorer", event_id, None
            )
            if awarded:
                newly_awarded.append(awarded)

        # Check wins in each format
        has_sf_win = stats.total_wins > 0
        has_ta_win = stats.ta_tournament_wins is not None and stats.ta_tournament_wins > 0
        has_tsf_win = stats.tsf_tournament_wins is not None and stats.tsf_tournament_wins > 0

        # Triple Threat: Won in all 3 formats
        if has_sf_win and has_ta_win and has_tsf_win:
            awarded = await AchievementService._award_achievement(
                db, user_id, "triple_threat", event_id, None
            )
            if awarded:
                newly_awarded.append(awarded)

        # Check podiums in each format
        has_sf_podium = stats.podium_finishes > 0
        has_ta_podium = stats.ta_tournament_podiums is not None and stats.ta_tournament_podiums > 0
        has_tsf_podium = stats.tsf_tournament_podiums is not None and stats.tsf_tournament_podiums > 0

        # Versatile Angler: Podium in all 3 formats
        if has_sf_podium and has_ta_podium and has_tsf_podium:
            awarded = await AchievementService._award_achievement(
                db, user_id, "versatile_angler", event_id, None
            )
            if awarded:
                newly_awarded.append(awarded)

        return newly_awarded


# Singleton instance
achievement_service = AchievementService()
