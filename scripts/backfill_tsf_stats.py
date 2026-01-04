"""
Backfill script to populate TSF statistics for historical participants.

This script finds all users who have participated in completed TSF events
and recalculates their statistics to include TSF performance metrics.

Run with: python -m scripts.backfill_tsf_stats

Options:
  --verify-only  Only run verification, don't process users
  --batch-size N Process N users between commits (default: 50)
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, distinct, func

from app.database import async_session_maker
from app.models.trout_shore import TSFLineup
from app.models.event import Event, EventStatus
from app.models.statistics import UserEventTypeStats
from app.services.statistics_service import StatisticsService


async def get_tsf_participants(db) -> list[int]:
    """Get unique user IDs from completed TSF events."""
    result = await db.execute(
        select(distinct(TSFLineup.user_id))
        .join(Event, TSFLineup.event_id == Event.id)
        .where(TSFLineup.user_id.isnot(None))
        .where(TSFLineup.is_ghost == False)
        .where(Event.status == EventStatus.COMPLETED.value)
    )
    return [row[0] for row in result.fetchall()]


async def verify_tsf_stats(db) -> int:
    """Count users with populated TSF stats."""
    result = await db.execute(
        select(func.count())
        .select_from(UserEventTypeStats)
        .where(UserEventTypeStats.tsf_total_days.isnot(None))
    )
    return result.scalar() or 0


async def backfill(batch_size: int = 50, verify_only: bool = False):
    """Backfill TSF statistics for all users with TSF participation."""
    print("=" * 60)
    print("TSF Statistics Backfill Script")
    print("=" * 60)
    print()

    async with async_session_maker() as db:
        # 1. Get all TSF participants
        print("Finding users with TSF participation...")
        start_time = time.time()
        user_ids = await get_tsf_participants(db)
        print(f"  Found {len(user_ids)} users with completed TSF events")
        print()

        if not user_ids:
            print("No users to process. Exiting.")
            return

        # 2. Verify-only mode
        if verify_only:
            print("Verification mode - not processing users")
            print()
            tsf_stats_count = await verify_tsf_stats(db)
            print(f"Verification: {tsf_stats_count} users have TSF statistics")
            return

        # 3. Process each user
        print(f"Processing users (batch size: {batch_size})...")
        success_count = 0
        error_count = 0

        for i, user_id in enumerate(user_ids, 1):
            try:
                # Recalculate all stats for the user (includes TSF stats via Story 3.2)
                await StatisticsService.recalculate_all_stats(db, user_id)
                success_count += 1

            except Exception as e:
                print(f"  ERROR processing user {user_id}: {e}")
                error_count += 1
                continue

            # Progress logging every 10 users
            if i % 10 == 0:
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                print(f"  Processed {i}/{len(user_ids)} users... ({rate:.1f} users/sec)")

            # Batch commit
            if i % batch_size == 0:
                await db.commit()

        # Final commit
        await db.commit()

        elapsed = time.time() - start_time
        print()
        print("-" * 60)
        print(f"Processing complete in {elapsed:.1f} seconds")
        print(f"  Success: {success_count}/{len(user_ids)} users")
        print(f"  Errors:  {error_count}/{len(user_ids)} users")
        print()

        # 4. Verification step
        print("Running verification...")
        tsf_stats_count = await verify_tsf_stats(db)
        print(f"  {tsf_stats_count} users now have TSF statistics")

    print()
    print("=" * 60)
    print("Backfill completed successfully!")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Backfill TSF statistics for historical participants")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only run verification, don't process users"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of users to process between commits (default: 50)"
    )
    args = parser.parse_args()

    asyncio.run(backfill(
        batch_size=args.batch_size,
        verify_only=args.verify_only
    ))


if __name__ == "__main__":
    main()
