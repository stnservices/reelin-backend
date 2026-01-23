#!/usr/bin/env python3
"""Script to recalculate achievements for a specific user.

Usage:
    python scripts/recalculate_user_achievements.py 262
"""

import asyncio
import sys
from datetime import timedelta

# Add parent directory to path for imports
sys.path.insert(0, ".")

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session_maker
from app.models.user import UserAccount
from app.models.event import Event
from app.models.catch import Catch, CatchStatus
from app.models.enrollment import EventEnrollment
from app.models.achievement import UserAchievement
from app.services.achievement_service import AchievementService
from app.tasks.achievements import send_achievement_notification


async def recalculate_user_achievements(user_id: int):
    """Recalculate all achievements for a user."""
    async with async_session_maker() as db:
        # Verify user exists
        user = await db.get(UserAccount, user_id)
        if not user:
            print(f"❌ User {user_id} not found")
            return

        print(f"🎯 Recalculating achievements for user {user_id} ({user.email})")

        # Get current achievement count
        before_count_result = await db.execute(
            select(func.count(UserAchievement.id)).where(UserAchievement.user_id == user_id)
        )
        achievements_before = before_count_result.scalar() or 0
        print(f"📊 Current achievements: {achievements_before}")

        # Get user's completed events (non-test)
        events_query = (
            select(Event.id, Event.event_type_id)
            .join(EventEnrollment, EventEnrollment.event_id == Event.id)
            .where(EventEnrollment.user_id == user_id)
            .where(EventEnrollment.status == "approved")
            .where(Event.status == "completed")
            .where(Event.is_test == False)
            .order_by(Event.end_date)
        )
        events_result = await db.execute(events_query)
        events = events_result.fetchall()
        print(f"📅 Found {len(events)} completed events")

        new_achievements = []

        # Get user's approved catches from non-test events with fish info
        catches_query = (
            select(Catch)
            .join(Event, Event.id == Catch.event_id)
            .options(selectinload(Catch.fish))
            .where(Catch.user_id == user_id)
            .where(Catch.status == CatchStatus.APPROVED.value)
            .where(Event.is_test == False)
            .order_by(Catch.submitted_at)
        )
        catches_result = await db.execute(catches_query)
        catches = catches_result.scalars().all()
        print(f"🐟 Found {len(catches)} approved catches")

        # Trigger catch_approved for each catch
        max_length_seen = 0.0

        for i, catch in enumerate(catches):
            event = await db.get(Event, catch.event_id)
            if event and event.event_type:
                format_code = "sf" if event.event_type.code == "street_fishing" else "ta"

                # Build context
                catch_time = catch.catch_time or catch.submitted_at
                early_cutoff = event.start_date + timedelta(minutes=30)
                is_early_bird = catch_time <= early_cutoff if catch_time and event.start_date else False
                late_cutoff = event.end_date - timedelta(minutes=30)
                is_last_minute = catch_time >= late_cutoff if catch_time and event.end_date else False
                is_personal_best = catch.length > max_length_seen if catch.length else False
                if catch.length and catch.length > max_length_seen:
                    max_length_seen = catch.length

                context = {
                    "catch_length": catch.length,
                    "catch_weight": catch.weight,
                    "fish_id": catch.fish_id,
                    "fish_slug": catch.fish.slug if catch.fish else None,
                    "is_early_bird": is_early_bird,
                    "is_last_minute": is_last_minute,
                    "is_personal_best": is_personal_best,
                }

                awarded = await AchievementService.check_and_award_achievements(
                    db,
                    user_id=user_id,
                    trigger="catch_approved",
                    event_id=catch.event_id,
                    catch_id=catch.id,
                    context=context,
                    format_code=format_code,
                )
                for ach in awarded:
                    if ach.code not in new_achievements:
                        new_achievements.append(ach.code)
                        print(f"  🏆 NEW: {ach.name} ({ach.code})")
                        # Send notification
                        send_achievement_notification.delay(user_id, ach.id, catch.event_id)

            if (i + 1) % 50 == 0:
                print(f"  ... processed {i + 1}/{len(catches)} catches")

        # Trigger event_completed for each completed event
        for event_row in events:
            event = await db.get(Event, event_row.id)
            if event and event.event_type:
                format_code = "sf" if event.event_type.code == "street_fishing" else "ta"
                awarded = await AchievementService.check_and_award_achievements(
                    db,
                    user_id=user_id,
                    trigger="event_completed",
                    event_id=event_row.id,
                    format_code=format_code,
                )
                for ach in awarded:
                    if ach.code not in new_achievements:
                        new_achievements.append(ach.code)
                        print(f"  🏆 NEW: {ach.name} ({ach.code})")
                        send_achievement_notification.delay(user_id, ach.id, event_row.id)

        await db.commit()

        # Get final achievement count
        after_count_result = await db.execute(
            select(func.count(UserAchievement.id)).where(UserAchievement.user_id == user_id)
        )
        achievements_after = after_count_result.scalar() or 0

        print(f"\n✅ Recalculation complete!")
        print(f"   Before: {achievements_before}")
        print(f"   After:  {achievements_after}")
        print(f"   New:    {len(new_achievements)}")
        if new_achievements:
            print(f"   Codes:  {', '.join(new_achievements)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/recalculate_user_achievements.py <user_id>")
        sys.exit(1)

    user_id = int(sys.argv[1])
    asyncio.run(recalculate_user_achievements(user_id))
