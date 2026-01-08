"""
Seed script to populate initial data for ReelIn application.

Run with: python -m scripts.seed_data
Or from Docker: docker-compose exec backend python -m scripts.seed_data
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker, init_db
from app.core.security import get_password_hash
from app.models.user import UserAccount, UserProfile
from app.models.location import Country, City, FishingSpot
from app.models.fish import Fish
from app.models.event import EventType, ScoringConfig
from app.models.sponsor import Sponsor


async def seed_countries_and_cities(db: AsyncSession) -> dict[str, int]:
    """Seed countries and cities."""
    print("Seeding countries and cities...")

    countries_data = [
        {"name": "Romania", "code": "ROU"},
        {"name": "Hungary", "code": "HUN"},
        {"name": "Bulgaria", "code": "BGR"},
        {"name": "Serbia", "code": "SRB"},
        {"name": "Ukraine", "code": "UKR"},
    ]

    country_ids = {}
    for data in countries_data:
        # Check if exists
        result = await db.execute(select(Country).where(Country.code == data["code"]))
        country = result.scalar_one_or_none()
        if not country:
            country = Country(**data)
            db.add(country)
            await db.flush()
        country_ids[data["code"]] = country.id

    # Romanian cities
    romania_cities = [
        "Bucharest", "Cluj-Napoca", "Timisoara", "Iasi", "Constanta",
        "Brasov", "Craiova", "Galati", "Oradea", "Sibiu"
    ]

    for city_name in romania_cities:
        result = await db.execute(
            select(City).where(City.name == city_name, City.country_id == country_ids["ROU"])
        )
        if not result.scalar_one_or_none():
            city = City(name=city_name, country_id=country_ids["ROU"])
            db.add(city)

    await db.commit()
    print(f"  Created {len(countries_data)} countries and {len(romania_cities)} cities")
    return country_ids


async def seed_fish_species(db: AsyncSession) -> None:
    """Seed fish species."""
    print("Seeding fish species...")

    fish_data = [
        {"name": "Carp", "scientific_name": "Cyprinus carpio", "min_length": 30, "max_length": 100},
        {"name": "Pike", "scientific_name": "Esox lucius", "min_length": 40, "max_length": 130},
        {"name": "Perch", "scientific_name": "Perca fluviatilis", "min_length": 15, "max_length": 50},
        {"name": "Catfish", "scientific_name": "Silurus glanis", "min_length": 50, "max_length": 250},
        {"name": "Zander", "scientific_name": "Sander lucioperca", "min_length": 35, "max_length": 100},
        {"name": "Bream", "scientific_name": "Abramis brama", "min_length": 20, "max_length": 60},
        {"name": "Roach", "scientific_name": "Rutilus rutilus", "min_length": 10, "max_length": 40},
        {"name": "Chub", "scientific_name": "Squalius cephalus", "min_length": 15, "max_length": 60},
        {"name": "Asp", "scientific_name": "Leuciscus aspius", "min_length": 30, "max_length": 80},
        {"name": "Trout", "scientific_name": "Salmo trutta", "min_length": 20, "max_length": 80},
        {"name": "Rainbow Trout", "scientific_name": "Oncorhynchus mykiss", "min_length": 20, "max_length": 70},
        {"name": "Grass Carp", "scientific_name": "Ctenopharyngodon idella", "min_length": 40, "max_length": 120},
        {"name": "Silver Carp", "scientific_name": "Hypophthalmichthys molitrix", "min_length": 35, "max_length": 100},
        {"name": "Tench", "scientific_name": "Tinca tinca", "min_length": 20, "max_length": 60},
        {"name": "Crucian Carp", "scientific_name": "Carassius carassius", "min_length": 15, "max_length": 40},
    ]

    created = 0
    for data in fish_data:
        result = await db.execute(select(Fish).where(Fish.name == data["name"]))
        if not result.scalar_one_or_none():
            fish = Fish(**data)
            db.add(fish)
            created += 1

    await db.commit()
    print(f"  Created {created} fish species")


async def seed_event_types(db: AsyncSession) -> dict[str, int]:
    """Seed event types and scoring configurations."""
    print("Seeding event types and scoring configurations...")

    event_types_data = [
        {
            "name": "Street Fishing",
            "code": "street_fishing",
            "description": "Urban fishing competitions in city waters",
        },
        {
            "name": "Trout Area",
            "code": "trout_area",
            "description": "Trout fishing in designated areas with match format",
        },
    ]

    type_ids = {}
    for data in event_types_data:
        result = await db.execute(select(EventType).where(EventType.code == data["code"]))
        event_type = result.scalar_one_or_none()
        if not event_type:
            event_type = EventType(**data)
            db.add(event_type)
            await db.flush()
        type_ids[data["code"]] = event_type.id

    await db.commit()

    # Get event type objects for M2M relationships
    event_type_objects = {}
    for code in type_ids.keys():
        result = await db.execute(select(EventType).where(EventType.code == code))
        event_type_objects[code] = result.scalar_one()

    # Scoring configurations with M2M event type assignments
    # Note: Same scoring type can be assigned to multiple event types
    scoring_configs = [
        # Street Fishing scoring types
        {
            "name": "Top X Catches",
            "code": "sf_top_x_overall",
            "description": "Score based on top X catches by length, regardless of species",
            "rules": {
                "top_count": 5,
                "measure": "length",
                "tie_breaker": "catch_time",
                "scoring_method": "top_n_overall",
            },
            "event_types": ["street_fishing"],
        },
        {
            "name": "Top X by Species",
            "code": "sf_top_x_by_species",
            "description": "Score based on top X catches per species slot",
            "rules": {
                "top_count": 5,
                "measure": "length",
                "species_slots": True,
                "tie_breaker": "total_length",
                "scoring_method": "top_n_by_species",
            },
            "event_types": ["street_fishing"],
        },
        # Trout Area - Match based (NOT YET IMPLEMENTED)
        {
            "name": "Match Format",
            "code": "ta_match",
            "description": "Head-to-head match scoring (requires lineup + game cards)",
            "rules": {
                "scoring_type": "match",
                "points_win": 3.0,
                "points_tie_fish": 1.5,
                "points_tie_no_fish": 1.0,
                "points_loss_fish": 0.5,
                "points_loss_no_fish": 0.0,
            },
            "event_types": ["trout_area"],
        },
    ]

    created = 0
    for data in scoring_configs:
        result = await db.execute(
            select(ScoringConfig).where(ScoringConfig.code == data["code"])
        )
        existing = result.scalar_one_or_none()
        if not existing:
            # Extract event_types for M2M
            event_type_codes = data.pop("event_types", [])
            config = ScoringConfig(**data)

            # Assign event types
            for et_code in event_type_codes:
                if et_code in event_type_objects:
                    config.event_types.append(event_type_objects[et_code])

            db.add(config)
            created += 1

    await db.commit()
    print(f"  Created {len(event_types_data)} event types and {created} scoring configs")
    return type_ids


async def seed_admin_user(db: AsyncSession) -> None:
    """Create default admin user."""
    print("Creating admin user...")

    # Check if admin exists
    result = await db.execute(
        select(UserAccount).where(UserAccount.email == "admin@reelin.ro")
    )
    if result.scalar_one_or_none():
        print("  Admin user already exists")
        return

    # Create admin account
    admin = UserAccount(
        email="admin@reelin.ro",
        password_hash=get_password_hash("Admin123!"),  # Change this in production!
        is_active=True,
        is_verified=True,
        is_staff=True,
        is_superuser=True,
    )
    db.add(admin)
    await db.flush()

    # Create admin profile
    profile = UserProfile(
        user_id=admin.id,
        first_name="System",
        last_name="Administrator",
        roles=["administrator", "organizer", "validator"],
    )
    db.add(profile)
    await db.commit()

    print("  Created admin user: admin@reelin.ro / Admin123!")


async def seed_test_users(db: AsyncSession) -> None:
    """Create test users for different roles."""
    print("Creating test users...")

    test_users = [
        {
            "email": "organizer@reelin.ro",
            "password": "Organizer123!",
            "first_name": "Test",
            "last_name": "Organizer",
            "roles": ["organizer"],
        },
        {
            "email": "validator@reelin.ro",
            "password": "Validator123!",
            "first_name": "Test",
            "last_name": "Validator",
            "roles": ["validator"],
        },
        {
            "email": "angler@reelin.ro",
            "password": "Angler123!",
            "first_name": "Test",
            "last_name": "Angler",
            "roles": ["angler"],
        },
    ]

    created = 0
    for user_data in test_users:
        result = await db.execute(
            select(UserAccount).where(UserAccount.email == user_data["email"])
        )
        if result.scalar_one_or_none():
            continue

        user = UserAccount(
            email=user_data["email"],
            password_hash=get_password_hash(user_data["password"]),
            is_active=True,
            is_verified=True,
        )
        db.add(user)
        await db.flush()

        profile = UserProfile(
            user_id=user.id,
            first_name=user_data["first_name"],
            last_name=user_data["last_name"],
            roles=user_data["roles"],
        )
        db.add(profile)
        created += 1

    await db.commit()
    print(f"  Created {created} test users")


async def seed_sponsors(db: AsyncSession) -> None:
    """Seed sample sponsors."""
    print("Seeding sponsors...")

    sponsors_data = [
        {"name": "FishingGear Pro", "website_url": "https://example.com", "display_order": 1},
        {"name": "Lake Masters", "website_url": "https://example.com", "display_order": 2},
        {"name": "Angler's Choice", "website_url": "https://example.com", "display_order": 3},
    ]

    created = 0
    for data in sponsors_data:
        result = await db.execute(select(Sponsor).where(Sponsor.name == data["name"]))
        if not result.scalar_one_or_none():
            sponsor = Sponsor(**data)
            db.add(sponsor)
            created += 1

    await db.commit()
    print(f"  Created {created} sponsors")


async def main():
    """Run all seed functions."""
    print("=" * 50)
    print("ReelIn Database Seeding")
    print("=" * 50)

    # Initialize database (create tables if needed)
    await init_db()

    async with async_session_maker() as db:
        await seed_countries_and_cities(db)
        await seed_fish_species(db)
        await seed_event_types(db)
        await seed_admin_user(db)
        await seed_test_users(db)
        await seed_sponsors(db)

    print("=" * 50)
    print("Seeding completed successfully!")
    print("=" * 50)
    print("\nTest Credentials:")
    print("  Admin: admin@reelin.ro / Admin123!")
    print("  Organizer: organizer@reelin.ro / Organizer123!")
    print("  Validator: validator@reelin.ro / Validator123!")
    print("  Angler: angler@reelin.ro / Angler123!")


if __name__ == "__main__":
    asyncio.run(main())
