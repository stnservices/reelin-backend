"""
QA Test Setup Script for TA Event with 20 users
Creates or reuses a TA event with 20 participants for full round robin testing.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, delete, func
from sqlalchemy.orm import selectinload

from app.database import async_session_maker, init_db
from app.models.event import Event, EventType
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.user import UserAccount as User, UserProfile
from app.core.security import get_password_hash


TARGET_PARTICIPANTS = 20
EVENT_NAME = "TA Full Round Test (20 users)"


async def create_or_get_user(db, email: str, display_name: str) -> User:
    """Create a user if it doesn't exist, otherwise return existing."""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user:
        return user

    # Create user account
    user = User(
        email=email,
        password_hash=get_password_hash("password123"),
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    await db.flush()

    # Create user profile with display name
    # Split display name into first/last
    parts = display_name.split(" ", 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""

    profile = UserProfile(
        user_id=user.id,
        first_name=first_name,
        last_name=last_name,
    )
    db.add(profile)
    await db.flush()

    return user


async def setup_ta_test_event():
    await init_db()

    async with async_session_maker() as db:
        print("=" * 60)
        print("TA Test Setup - 20 Participants Full Round Robin")
        print("=" * 60)

        # 1. Get Trout Area event type
        et_result = await db.execute(
            select(EventType).where(EventType.format_code == "ta")
        )
        event_type = et_result.scalar_one_or_none()
        if not event_type:
            print("ERROR: Trout Area event type not found!")
            return

        print(f"\nEvent Type: {event_type.name} (format: {event_type.format_code})")

        # 2. Check if test event exists
        event_result = await db.execute(
            select(Event).where(Event.name == EVENT_NAME)
        )
        event = event_result.scalar_one_or_none()

        if event:
            print(f"\nFound existing event: {event.name} (ID: {event.id})")
            # Reset to draft if needed
            if event.status != "draft":
                event.status = "draft"
                print(f"  Reset status to draft")
        else:
            # Get a reference TA event to copy scoring_config
            ref_result = await db.execute(
                select(Event).where(Event.event_type_id == event_type.id).limit(1)
            )
            ref_event = ref_result.scalar_one_or_none()
            scoring_config_id = ref_event.scoring_config_id if ref_event else 1

            # Create unique slug
            import uuid
            slug = f"ta-full-round-test-{uuid.uuid4().hex[:8]}"

            # Create new event
            event = Event(
                name=EVENT_NAME,
                slug=slug,
                event_type_id=event_type.id,
                scoring_config_id=scoring_config_id,
                created_by_id=1,  # admin
                status="draft",
                start_date=datetime.now(timezone.utc) + timedelta(hours=1),
                end_date=datetime.now(timezone.utc) + timedelta(hours=8),
                location_name="Test Lake",
                max_participants=50,
                is_team_event=False,
            )
            db.add(event)
            await db.flush()
            print(f"\nCreated new event: {event.name} (ID: {event.id})")

        await db.commit()
        await db.refresh(event)

        # 3. Clean existing enrollments for this event
        print("\n--- Cleaning existing enrollments ---")
        await db.execute(
            delete(EventEnrollment).where(EventEnrollment.event_id == event.id)
        )
        await db.commit()
        print("  Deleted existing enrollments")

        # 4. Create/get users and enroll them
        print(f"\n--- Creating/enrolling {TARGET_PARTICIPANTS} users ---")

        enrollments = []
        for i in range(1, TARGET_PARTICIPANTS + 1):
            email = f"user{i}@reelin.ro"
            display_name = f"TestUser{i}"

            user = await create_or_get_user(db, email, display_name)

            # Create enrollment
            enrollment = EventEnrollment(
                event_id=event.id,
                user_id=user.id,
                status=EnrollmentStatus.APPROVED.value,
                approved_at=datetime.now(timezone.utc),
            )
            db.add(enrollment)
            enrollments.append((user, enrollment))
            print(f"  {i}. {display_name} ({email}) - User ID: {user.id}")

        await db.commit()

        # 5. Verify
        print("\n" + "=" * 60)
        print("SETUP COMPLETE")
        print("=" * 60)

        count_result = await db.execute(
            select(func.count(EventEnrollment.id))
            .where(
                EventEnrollment.event_id == event.id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value
            )
        )
        enrollment_count = count_result.scalar()

        print(f"\nEvent ID: {event.id}")
        print(f"Event Name: {event.name}")
        print(f"Status: {event.status}")
        print(f"Approved Participants: {enrollment_count}")

        print(f"\n--- Next Steps ---")
        print(f"1. Generate lineups:")
        print(f"   POST /api/v1/ta/events/{event.id}/lineups/generate")
        print(f"   Body: {{\"algorithm\": \"round_robin_full\"}}")
        print(f"")
        print(f"2. Publish event:")
        print(f"   POST /api/v1/events/{event.id}/publish")
        print(f"")
        print(f"3. Start event:")
        print(f"   POST /api/v1/events/{event.id}/start")
        print(f"")
        print(f"4. View schedule:")
        print(f"   GET /api/v1/ta/events/{event.id}/schedule")

        return event.id


if __name__ == "__main__":
    event_id = asyncio.run(setup_ta_test_event())
    if event_id:
        print(f"\n✅ Event ID: {event_id}")
