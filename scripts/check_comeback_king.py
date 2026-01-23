#!/usr/bin/env python3
"""Check which users qualify for Comeback King but don't have the achievement."""

import asyncio
import sys
sys.path.insert(0, ".")

from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload

from app.database import async_session_maker
from app.models.event import Event
from app.models.catch import RankingMovement, EventScoreboard
from app.models.achievement import AchievementDefinition, UserAchievement
from app.models.user import UserAccount


async def check_comeback_king():
    """Find users who qualify for Comeback King but don't have it."""
    async with async_session_maker() as db:
        # Get all completed non-team events
        events_stmt = (
            select(Event)
            .where(Event.status == "completed")
            .where(Event.is_team_event == False)
            .where(Event.is_test == False)
        )
        result = await db.execute(events_stmt)
        events = result.scalars().all()

        print(f"Checking {len(events)} completed individual events...\n")

        # Get comeback_king achievement IDs
        ach_stmt = select(AchievementDefinition.id, AchievementDefinition.code).where(
            AchievementDefinition.code.like("%comeback_king%")
        )
        result = await db.execute(ach_stmt)
        comeback_achievements = {row.code: row.id for row in result.fetchall()}
        print(f"Comeback King achievement codes: {list(comeback_achievements.keys())}\n")

        qualified_users = []

        for event in events:
            # Get all users with rank movements in this event
            movements_stmt = (
                select(
                    RankingMovement.user_id,
                    func.max(RankingMovement.old_rank).label("max_old"),
                    func.max(RankingMovement.new_rank).label("max_new"),
                )
                .where(RankingMovement.event_id == event.id)
                .where(RankingMovement.user_id.isnot(None))
                .group_by(RankingMovement.user_id)
            )
            result = await db.execute(movements_stmt)
            user_movements = result.fetchall()

            for row in user_movements:
                user_id = row.user_id
                worst_rank = max(row.max_old or 0, row.max_new or 0)

                if worst_rank == 0:
                    continue

                # Get final rank
                final_stmt = (
                    select(EventScoreboard.rank)
                    .where(EventScoreboard.event_id == event.id)
                    .where(EventScoreboard.user_id == user_id)
                )
                result = await db.execute(final_stmt)
                final_rank = result.scalar()

                if final_rank is None:
                    continue

                improvement = worst_rank - final_rank

                if improvement >= 5:
                    # Check if user has the achievement
                    has_ach_stmt = (
                        select(UserAchievement.id)
                        .where(UserAchievement.user_id == user_id)
                        .where(UserAchievement.event_id == event.id)
                        .where(UserAchievement.achievement_id.in_(comeback_achievements.values()))
                    )
                    result = await db.execute(has_ach_stmt)
                    has_achievement = result.scalar() is not None

                    # Get user email
                    user_stmt = select(UserAccount.email).where(UserAccount.id == user_id)
                    result = await db.execute(user_stmt)
                    email = result.scalar()

                    qualified_users.append({
                        "event_id": event.id,
                        "event_name": event.name,
                        "user_id": user_id,
                        "email": email,
                        "worst_rank": worst_rank,
                        "final_rank": final_rank,
                        "improvement": improvement,
                        "has_achievement": has_achievement,
                    })

        # Sort by improvement descending
        qualified_users.sort(key=lambda x: x["improvement"], reverse=True)

        print(f"{'Event':<40} {'User':<35} {'Worst':<6} {'Final':<6} {'Improv':<7} {'Has Ach?'}")
        print("-" * 110)

        missing_count = 0
        for u in qualified_users:
            status = "✅ YES" if u["has_achievement"] else "❌ NO"
            if not u["has_achievement"]:
                missing_count += 1
            print(f"{u['event_name'][:38]:<40} {u['email'][:33]:<35} {u['worst_rank']:<6} {u['final_rank']:<6} {u['improvement']:<7} {status}")

        print(f"\nTotal qualified: {len(qualified_users)}")
        print(f"Missing achievement: {missing_count}")
        print(f"Already have it: {len(qualified_users) - missing_count}")


if __name__ == "__main__":
    asyncio.run(check_comeback_king())
