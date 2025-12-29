"""
QA Test Script - Simulate Catch Uploads for Event 4
Creates pending catches for all participants so validators can approve them.
"""

import asyncio
import random
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func

from app.database import async_session_maker, init_db
from app.models.event import Event, EventFishScoring
from app.models.fish import Fish
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.catch import Catch, CatchStatus
from app.models.rules import OrganizerRule, OrganizerRuleDefault  # Required for Event relationship


EVENT_ID = 4
CATCHES_PER_PARTICIPANT = 3  # 3 catches per participant


async def simulate_catches():
    await init_db()

    async with async_session_maker() as db:
        print("=" * 60)
        print("Simulating Catch Uploads for Event 4")
        print("=" * 60)

        # Get event
        event_result = await db.execute(
            select(Event).where(Event.id == EVENT_ID)
        )
        event = event_result.scalar_one_or_none()
        if not event:
            print(f"ERROR: Event {EVENT_ID} not found!")
            return

        print(f"\nEvent: {event.name}")

        # Get fish species for this event
        fish_result = await db.execute(
            select(EventFishScoring, Fish)
            .join(Fish, EventFishScoring.fish_id == Fish.id)
            .where(EventFishScoring.event_id == EVENT_ID)
        )
        fish_configs = fish_result.fetchall()

        if not fish_configs:
            print("ERROR: No fish species configured for this event!")
            return

        print(f"Fish species available: {len(fish_configs)}")
        fish_list = [(fc[0], fc[1]) for fc in fish_configs]

        # Get all approved enrollments
        enrollments_result = await db.execute(
            select(EventEnrollment)
            .where(
                EventEnrollment.event_id == EVENT_ID,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value
            )
        )
        enrollments = enrollments_result.scalars().all()

        print(f"Participants: {len(enrollments)}")

        # Delete existing catches for this event
        existing_count_result = await db.execute(
            select(func.count(Catch.id)).where(Catch.event_id == EVENT_ID)
        )
        existing_count = existing_count_result.scalar()

        if existing_count > 0:
            print(f"\nDeleting {existing_count} existing catches...")
            from sqlalchemy import delete
            await db.execute(delete(Catch).where(Catch.event_id == EVENT_ID))
            await db.commit()

        # Create catches for each participant
        print(f"\n--- Creating {CATCHES_PER_PARTICIPANT} catches per participant ---")

        total_catches = 0
        base_time = datetime.now(timezone.utc) - timedelta(hours=2)  # Catches from 2 hours ago

        for enrollment in enrollments:
            # Randomly select fish species for this participant (for diversity testing)
            selected_fish = random.sample(fish_list, min(CATCHES_PER_PARTICIPANT, len(fish_list)))

            for i, (fish_config, fish) in enumerate(selected_fish):
                # Generate random catch data
                min_length = fish_config.accountable_min_length
                length = round(random.uniform(min_length, min_length + 30), 1)  # Random length
                weight = round(length * 0.02, 2)  # Approximate weight based on length

                catch_time = base_time + timedelta(minutes=random.randint(0, 120))

                catch = Catch(
                    event_id=EVENT_ID,
                    user_id=enrollment.user_id,
                    fish_id=fish.id,
                    length=length,
                    weight=weight,
                    catch_time=catch_time,
                    status=CatchStatus.PENDING.value,
                    photo_url=f"https://placeholder.com/catch_{enrollment.id}_{i}.jpg",  # Placeholder
                )
                db.add(catch)
                total_catches += 1

        await db.commit()

        print(f"\nCreated {total_catches} catches for {len(enrollments)} participants")

        # Verify catch distribution
        print("\n--- Catch Statistics ---")

        # Count by status
        status_result = await db.execute(
            select(Catch.status, func.count(Catch.id))
            .where(Catch.event_id == EVENT_ID)
            .group_by(Catch.status)
        )
        print("\nBy Status:")
        for status, count in status_result.fetchall():
            print(f"  {status}: {count}")

        # Count by fish species
        species_result = await db.execute(
            select(Fish.name, func.count(Catch.id))
            .join(Fish, Catch.fish_id == Fish.id)
            .where(Catch.event_id == EVENT_ID)
            .group_by(Fish.name)
        )
        print("\nBy Species:")
        for name, count in species_result.fetchall():
            print(f"  {name}: {count}")

        print("\n" + "=" * 60)
        print("Catch simulation complete!")
        print(f"Total catches created: {total_catches}")
        print(f"All catches are in PENDING status - ready for validator approval")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(simulate_catches())
