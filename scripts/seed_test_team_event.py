"""
Seed script to create 100 test users and enroll them to a team test event.

Run with: python -m scripts.seed_test_team_event
Or from Docker: docker-compose exec backend python -m scripts.seed_test_team_event
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker, init_db
from app.core.security import get_password_hash
from app.models.user import UserAccount, UserProfile
from app.models.event import Event, EventType, ScoringConfig, EventStatus
from app.models.enrollment import EventEnrollment, EnrollmentStatus


# Configuration
NUM_USERS = 100
EMAIL_DOMAIN = "reelin.ro"
DEFAULT_PASSWORD = "test123"
EVENT_NAME = "Test Team Event 2024"


async def create_test_users(db: AsyncSession) -> list[int]:
    """Create 100 test users (user1 to user100)."""
    print(f"Creating {NUM_USERS} test users...")

    password_hash = get_password_hash(DEFAULT_PASSWORD)
    user_ids = []

    for i in range(1, NUM_USERS + 1):
        email = f"user{i}@{EMAIL_DOMAIN}"

        # Check if user already exists
        result = await db.execute(select(UserAccount).where(UserAccount.email == email))
        existing_user = result.scalar_one_or_none()

        if existing_user:
            user_ids.append(existing_user.id)
            continue

        # Create user account
        user = UserAccount(
            email=email,
            password_hash=password_hash,
            is_active=True,
            is_verified=True,
        )
        db.add(user)
        await db.flush()

        # Create user profile
        profile = UserProfile(
            user_id=user.id,
            first_name=f"Test",
            last_name=f"User{i}",
            roles=["angler"],
        )
        db.add(profile)
        user_ids.append(user.id)

        if i % 20 == 0:
            print(f"  Created {i}/{NUM_USERS} users...")

    await db.commit()
    print(f"  Created/verified {len(user_ids)} users")
    return user_ids


async def create_team_event(db: AsyncSession) -> int:
    """Create a team event for testing."""
    print(f"Creating team event: {EVENT_NAME}...")

    # Check if event already exists
    result = await db.execute(select(Event).where(Event.name == EVENT_NAME))
    existing_event = result.scalar_one_or_none()

    if existing_event:
        print(f"  Event already exists with ID: {existing_event.id}")
        return existing_event.id

    # Get event type (Street Fishing)
    result = await db.execute(select(EventType).where(EventType.code == "street_fishing"))
    event_type = result.scalar_one_or_none()

    if not event_type:
        print("  ERROR: Street Fishing event type not found. Run seed_data.py first.")
        return None

    # Get scoring config
    result = await db.execute(select(ScoringConfig).limit(1))
    scoring_config = result.scalar_one_or_none()

    if not scoring_config:
        print("  ERROR: No scoring config found. Run seed_data.py first.")
        return None

    # Get admin user for created_by
    result = await db.execute(select(UserAccount).where(UserAccount.email == "admin@reelin.ro"))
    admin = result.scalar_one_or_none()

    if not admin:
        print("  ERROR: Admin user not found. Run seed_data.py first.")
        return None

    # Create event
    now = datetime.now(timezone.utc)
    event = Event(
        name=EVENT_NAME,
        slug=f"test-team-event-{int(now.timestamp())}",
        description="A test event for team management functionality testing with 100 enrolled participants.",
        event_type_id=event_type.id,
        scoring_config_id=scoring_config.id,
        created_by_id=admin.id,
        status=EventStatus.PUBLISHED.value,
        start_date=now + timedelta(days=7),
        end_date=now + timedelta(days=8),
        registration_deadline=now + timedelta(days=6),
        location_name="Test Location",
        is_team_event=True,
        min_team_size=2,
        max_team_size=10,
        max_participants=150,
        requires_approval=False,  # Auto-approve for testing
        published_at=now,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    print(f"  Created event with ID: {event.id}")
    return event.id


async def enroll_users_to_event(db: AsyncSession, event_id: int, user_ids: list[int]) -> None:
    """Enroll all test users to the event and approve them."""
    print(f"Enrolling {len(user_ids)} users to event {event_id}...")

    enrolled_count = 0
    for i, user_id in enumerate(user_ids, 1):
        # Check if already enrolled
        result = await db.execute(
            select(EventEnrollment).where(
                EventEnrollment.event_id == event_id,
                EventEnrollment.user_id == user_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Make sure it's approved
            if existing.status != EnrollmentStatus.APPROVED.value:
                existing.status = EnrollmentStatus.APPROVED.value
                existing.approved_at = datetime.now(timezone.utc)
            continue

        # Create enrollment with approved status
        enrollment = EventEnrollment(
            event_id=event_id,
            user_id=user_id,
            status=EnrollmentStatus.APPROVED.value,
            approved_at=datetime.now(timezone.utc),
            draw_number=i,
        )
        db.add(enrollment)
        enrolled_count += 1

        if i % 20 == 0:
            print(f"  Enrolled {i}/{len(user_ids)} users...")

    await db.commit()
    print(f"  Enrolled {enrolled_count} new users (total: {len(user_ids)} approved)")


async def main():
    """Main function to run the seeding."""
    print("=" * 60)
    print("Seeding Test Team Event Data")
    print("=" * 60)

    # Initialize database
    await init_db()

    async with async_session_maker() as db:
        # Create test users
        user_ids = await create_test_users(db)

        if not user_ids:
            print("ERROR: Failed to create users")
            return

        # Create team event
        event_id = await create_team_event(db)

        if not event_id:
            print("ERROR: Failed to create event")
            return

        # Enroll users to event
        await enroll_users_to_event(db, event_id, user_ids)

    print("=" * 60)
    print("Test data seeding complete!")
    print(f"  - Created {NUM_USERS} users: user1@{EMAIL_DOMAIN} to user{NUM_USERS}@{EMAIL_DOMAIN}")
    print(f"  - Password for all users: {DEFAULT_PASSWORD}")
    print(f"  - Team event ID: {event_id}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
