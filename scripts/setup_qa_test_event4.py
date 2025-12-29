"""
QA Test Setup Script for Event 4
Sets up event 4 with:
- 40 participants (reduce from current)
- 8 teams with country names (5 members each)
- Fish species with scoring values
- Bonus points for species diversity
- All participants approved and assigned to teams
"""

import asyncio
import random
from datetime import datetime, timezone

from sqlalchemy import select, delete, func, text
from sqlalchemy.orm import selectinload

from app.database import async_session_maker, init_db
from app.models.event import Event, EventFishScoring, EventSpeciesBonusPoints
from app.models.fish import Fish
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.team import Team, TeamMember
from app.models.rules import OrganizerRule, OrganizerRuleDefault  # Required for Event relationship


EVENT_ID = 4
TARGET_PARTICIPANTS = 40
TEAM_SIZE = 5

COUNTRY_TEAMS = [
    "Romania",
    "Italy",
    "France",
    "Germany",
    "Spain",
    "Portugal",
    "Hungary",
    "Poland"
]


async def setup_test_event():
    await init_db()

    async with async_session_maker() as db:
        print("=" * 60)
        print("QA Test Setup for Event 4")
        print("=" * 60)

        # 1. Get event and verify it exists
        event_result = await db.execute(
            select(Event).where(Event.id == EVENT_ID)
        )
        event = event_result.scalar_one_or_none()
        if not event:
            print(f"ERROR: Event {EVENT_ID} not found!")
            return

        print(f"\nEvent: {event.name} (ID: {event.id})")
        print(f"  - is_team_event: {event.is_team_event}")
        print(f"  - has_bonus_points: {event.has_bonus_points}")
        print(f"  - min_team_size: {event.min_team_size}")
        print(f"  - max_team_size: {event.max_team_size}")

        # 2. Count current enrollments
        count_result = await db.execute(
            select(func.count(EventEnrollment.id))
            .where(EventEnrollment.event_id == EVENT_ID)
        )
        current_count = count_result.scalar()
        print(f"\nCurrent enrollments: {current_count}")

        # 3. Delete team members first (foreign key constraint)
        print("\n--- Cleaning up team members ---")
        team_ids_result = await db.execute(
            select(Team.id).where(Team.event_id == EVENT_ID)
        )
        team_ids = [t[0] for t in team_ids_result.fetchall()]

        if team_ids:
            await db.execute(
                delete(TeamMember).where(TeamMember.team_id.in_(team_ids))
            )
            print(f"  Deleted team members from {len(team_ids)} teams")

        # 4. Delete existing teams
        print("\n--- Cleaning up existing teams ---")
        await db.execute(
            delete(Team).where(Team.event_id == EVENT_ID)
        )
        print("  Deleted existing teams")

        # 5. Delete extra enrollments (keep first 40)
        print(f"\n--- Reducing enrollments to {TARGET_PARTICIPANTS} ---")

        # Get all enrollment IDs ordered by ID
        enrollments_result = await db.execute(
            select(EventEnrollment)
            .where(EventEnrollment.event_id == EVENT_ID)
            .order_by(EventEnrollment.id)
        )
        all_enrollments = enrollments_result.scalars().all()

        # Keep first 40, delete the rest
        enrollments_to_keep = all_enrollments[:TARGET_PARTICIPANTS]
        enrollments_to_delete = all_enrollments[TARGET_PARTICIPANTS:]

        if enrollments_to_delete:
            delete_ids = [e.id for e in enrollments_to_delete]
            await db.execute(
                delete(EventEnrollment).where(EventEnrollment.id.in_(delete_ids))
            )
            print(f"  Deleted {len(delete_ids)} excess enrollments")

        # 6. Ensure all remaining enrollments are approved
        print("\n--- Approving all enrollments ---")
        for enrollment in enrollments_to_keep:
            if enrollment.status != EnrollmentStatus.APPROVED.value:
                enrollment.status = EnrollmentStatus.APPROVED.value
                enrollment.approved_at = datetime.now(timezone.utc)
        print(f"  All {len(enrollments_to_keep)} enrollments are now approved")

        await db.commit()

        # 7. Create country teams
        print(f"\n--- Creating {len(COUNTRY_TEAMS)} country teams ---")
        teams = []
        for i, country in enumerate(COUNTRY_TEAMS):
            team = Team(
                event_id=EVENT_ID,
                name=country,
                created_by_id=1,  # admin
                is_active=True
            )
            db.add(team)
            teams.append(team)

        await db.commit()

        # Refresh to get IDs
        for team in teams:
            await db.refresh(team)
            print(f"  Created team: {team.name} (ID: {team.id})")

        # 8. Assign participants to teams (5 per team)
        print(f"\n--- Assigning {TARGET_PARTICIPANTS} participants to teams ---")

        # Re-fetch enrollments after commit
        enrollments_result = await db.execute(
            select(EventEnrollment)
            .where(EventEnrollment.event_id == EVENT_ID)
            .order_by(EventEnrollment.id)
            .limit(TARGET_PARTICIPANTS)
        )
        enrollments = enrollments_result.scalars().all()

        # Assign 5 members to each team
        for i, enrollment in enumerate(enrollments):
            team_index = i // TEAM_SIZE
            if team_index < len(teams):
                team = teams[team_index]
                role = "captain" if i % TEAM_SIZE == 0 else "member"

                team_member = TeamMember(
                    team_id=team.id,
                    enrollment_id=enrollment.id,
                    role=role,
                    added_by_id=1,  # admin
                    is_active=True
                )
                db.add(team_member)

        await db.commit()
        print(f"  Assigned {len(enrollments)} participants to {len(teams)} teams")

        # 9. Add fish species with scoring values
        print("\n--- Setting up fish species scoring ---")

        # Get available fish
        fish_result = await db.execute(select(Fish).where(Fish.is_active == True).limit(10))
        available_fish = fish_result.scalars().all()

        if not available_fish:
            print("  WARNING: No fish species found in database!")
        else:
            # Delete existing event fish scoring
            await db.execute(
                delete(EventFishScoring).where(EventFishScoring.event_id == EVENT_ID)
            )

            # Add fish with scoring values
            for i, fish in enumerate(available_fish[:5]):  # Use first 5 fish
                event_fish = EventFishScoring(
                    event_id=EVENT_ID,
                    fish_id=fish.id,
                    accountable_catch_slots=5,
                    accountable_min_length=20.0,  # 20cm minimum
                    under_min_length_points=0,
                    top_x_catches=5,
                    display_order=i + 1,
                )
                db.add(event_fish)
                print(f"  Added fish: {fish.name} (min: 20cm, 5 catch slots)")

            await db.commit()

        # 10. Add bonus points for species diversity
        print("\n--- Setting up bonus points ---")

        # Delete existing bonus points
        await db.execute(
            delete(EventSpeciesBonusPoints).where(EventSpeciesBonusPoints.event_id == EVENT_ID)
        )

        # Add species diversity bonus
        if available_fish:
            bonus_configs = [
                {"species_count": 3, "bonus_points": 50},
                {"species_count": 5, "bonus_points": 100},
            ]

            for config in bonus_configs:
                bonus = EventSpeciesBonusPoints(
                    event_id=EVENT_ID,
                    species_count=config["species_count"],
                    bonus_points=config["bonus_points"],
                )
                db.add(bonus)
                print(f"  Added bonus: Catch {config['species_count']} species = {config['bonus_points']} pts")

        await db.commit()

        # 11. Verify final state
        print("\n" + "=" * 60)
        print("FINAL STATE VERIFICATION")
        print("=" * 60)

        # Count enrollments
        final_count_result = await db.execute(
            select(func.count(EventEnrollment.id))
            .where(EventEnrollment.event_id == EVENT_ID)
        )
        final_enrollment_count = final_count_result.scalar()

        # Count approved
        approved_count_result = await db.execute(
            select(func.count(EventEnrollment.id))
            .where(
                EventEnrollment.event_id == EVENT_ID,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value
            )
        )
        approved_count = approved_count_result.scalar()

        # Count teams and members
        teams_count_result = await db.execute(
            select(func.count(Team.id)).where(Team.event_id == EVENT_ID)
        )
        teams_count = teams_count_result.scalar()

        team_members_result = await db.execute(
            select(func.count(TeamMember.id))
            .join(Team, TeamMember.team_id == Team.id)
            .where(Team.event_id == EVENT_ID, TeamMember.is_active == True)
        )
        members_count = team_members_result.scalar()

        # Count unassigned
        assigned_enrollment_ids = await db.execute(
            select(TeamMember.enrollment_id)
            .join(Team, TeamMember.team_id == Team.id)
            .where(Team.event_id == EVENT_ID, TeamMember.is_active == True)
        )
        assigned_ids = set(r[0] for r in assigned_enrollment_ids.fetchall())

        all_enrollment_ids = await db.execute(
            select(EventEnrollment.id)
            .where(EventEnrollment.event_id == EVENT_ID)
        )
        all_ids = set(r[0] for r in all_enrollment_ids.fetchall())

        unassigned_count = len(all_ids - assigned_ids)

        # Count fish species
        fish_count_result = await db.execute(
            select(func.count(EventFishScoring.id)).where(EventFishScoring.event_id == EVENT_ID)
        )
        fish_count = fish_count_result.scalar()

        # Count bonus points
        bonus_count_result = await db.execute(
            select(func.count(EventSpeciesBonusPoints.id)).where(EventSpeciesBonusPoints.event_id == EVENT_ID)
        )
        bonus_count = bonus_count_result.scalar()

        print(f"\nEnrollments: {final_enrollment_count} (all approved: {approved_count})")
        print(f"Teams: {teams_count} ({members_count} total members)")
        print(f"Unassigned participants: {unassigned_count}")
        print(f"Fish species configured: {fish_count}")
        print(f"Bonus point rules: {bonus_count}")

        # Check if ready for event start
        print("\n--- CONSTRAINT CHECKS ---")
        checks_passed = True

        if unassigned_count > 0:
            print(f"❌ FAIL: {unassigned_count} approved participant(s) not assigned to teams")
            checks_passed = False
        else:
            print("✅ PASS: All approved participants assigned to teams")

        if fish_count == 0:
            print("❌ FAIL: No fish species configured for event")
            checks_passed = False
        else:
            print(f"✅ PASS: {fish_count} fish species configured")

        if bonus_count == 0:
            print("⚠️ WARNING: No bonus points configured (optional)")
        else:
            print(f"✅ PASS: {bonus_count} bonus point rules configured")

        if checks_passed:
            print("\n✅ Event is ready for testing!")
        else:
            print("\n❌ Event setup incomplete - fix issues above")

        print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(setup_test_event())
