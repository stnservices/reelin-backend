"""
Data Migration Script: Old Reelin (Django) → New Reelin V2 (FastAPI)

Migrates data from the old PostgreSQL database to the new schema.

OLD DATABASE SCHEMA (Django):
  - accounts_useraccount     → user_accounts
  - profiles_userprofile     → user_profiles
  - competitions_event       → events
  - enrolment_eventenrolment → event_enrollments
  - competitions_eventphotocaptures → catches
  - fish_fish               → fish
  - teams_team              → teams
  - teams_teammember        → team_members
  - sponsor_sponsor         → sponsors
  - payments_payment        → (Stripe info)

USAGE:
  # Dry run (no changes, just report)
  python -m scripts.migrate_from_old --dry-run

  # Full migration
  python -m scripts.migrate_from_old

  # Migrate specific entities only
  python -m scripts.migrate_from_old --only users,events

  # With custom old database URL
  OLD_DATABASE_URL=postgresql://... python -m scripts.migrate_from_old

PREREQUISITES:
  1. New database must have migrations applied (alembic upgrade head)
  2. Production seed must be run (python -m scripts.seed_production)
  3. Old database must be accessible
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from decimal import Decimal

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncpg
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker, init_db
from app.core.security import get_password_hash
from app.models.user import UserAccount, UserProfile
from app.models.event import Event, EventType, ScoringConfig, EventFishScoring, EventSpeciesBonusPoints
from app.models.enrollment import EventEnrollment
from app.models.catch import Catch
from app.models.fish import Fish
from app.models.team import Team, TeamMember
from app.models.sponsor import Sponsor
from app.models.location import Country, City, FishingSpot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

# Old database connection - DigitalOcean managed PostgreSQL
OLD_DATABASE_URL = os.getenv(
    "OLD_DATABASE_URL",
    "postgresql://doadmin:PASSWORD@app-52321b3b-a3e0-40f1-bf4e-f4ed23272a7d-do-user-14096196-0.c.db.ondigitalocean.com:25060/db?sslmode=require"
)

# =============================================================================
# STATUS MAPPINGS
# =============================================================================

def map_event_status(row: dict) -> str:
    """Map old event status fields to new status enum."""
    if row.get("is_deleted"):
        return "cancelled"
    if row.get("is_ended"):
        return "completed"
    if row.get("is_ongoing"):
        return "ongoing"
    if row.get("is_active") and row.get("is_open_for_enrollment"):
        return "published"
    return "draft"


def map_enrollment_status(old_status: str, is_approved: bool) -> str:
    """Map old enrollment status to new status."""
    status_lower = (old_status or "").lower()

    if status_lower in ["approved", "accepted"] or is_approved:
        return "approved"
    if status_lower in ["rejected", "declined"]:
        return "rejected"
    if status_lower in ["cancelled", "withdrawn"]:
        return "cancelled"
    return "pending"


def map_catch_status(is_approved: bool, is_rejected: bool) -> str:
    """Map old catch approval fields to new status."""
    if is_approved:
        return "approved"
    if is_rejected:
        return "rejected"
    return "pending"


def map_user_roles(profile: dict) -> list[str]:
    """Map old profile role booleans to new roles array."""
    roles = []

    if profile.get("is_administrator"):
        roles.extend(["administrator", "organizer", "validator"])
    else:
        if profile.get("is_organiser"):
            roles.append("organizer")
        if profile.get("is_validator"):
            roles.append("validator")
        if profile.get("is_sponsor"):
            roles.append("sponsor")

    if profile.get("is_angler") or not roles:
        roles.append("angler")

    return list(set(roles))


def map_event_type_code(old_type_id: str) -> str:
    """Map old event_type_id to new event type code."""
    if not old_type_id:
        return "street_fishing"

    type_lower = old_type_id.lower()

    if "trout" in type_lower and "area" in type_lower:
        return "trout_area"
    if "trout" in type_lower and "shore" in type_lower:
        return "trout_shore"
    if "trout" in type_lower:
        return "trout_area"

    return "street_fishing"


def map_scoring_config_code(old_scoring_type: str, event_type_code: str) -> str:
    """Map old scoring_type_id to new scoring config code."""
    if not old_scoring_type:
        if event_type_code == "trout_area":
            return "ta_match"
        if event_type_code == "trout_shore":
            return "tsf_group"
        return "sf_top_x_by_species"

    scoring_lower = old_scoring_type.lower()

    if "overall" in scoring_lower or "top_x" in scoring_lower:
        return "sf_top_x_overall"
    if "species" in scoring_lower:
        return "sf_top_x_by_species"

    return "sf_top_x_by_species"


# =============================================================================
# MIGRATION STATS
# =============================================================================

class MigrationStats:
    """Track migration statistics."""

    def __init__(self):
        self.counts = {}
        self.errors: list[str] = []

    def add(self, entity: str, migrated: int = 0, skipped: int = 0):
        if entity not in self.counts:
            self.counts[entity] = {"migrated": 0, "skipped": 0}
        self.counts[entity]["migrated"] += migrated
        self.counts[entity]["skipped"] += skipped

    def print_summary(self):
        print("\n" + "=" * 60)
        print("MIGRATION SUMMARY")
        print("=" * 60)
        for entity, counts in self.counts.items():
            print(f"{entity:20}: {counts['migrated']:5} migrated, {counts['skipped']:5} skipped")

        if self.errors:
            print(f"\nErrors ({len(self.errors)}):")
            for err in self.errors[:20]:
                print(f"  - {err}")
            if len(self.errors) > 20:
                print(f"  ... and {len(self.errors) - 20} more")


# =============================================================================
# DATA MIGRATOR
# =============================================================================

class DataMigrator:
    """Handles data migration from old Django DB to new FastAPI DB."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.stats = MigrationStats()
        self.old_conn: Optional[asyncpg.Connection] = None
        self.new_db: Optional[AsyncSession] = None

        # ID mappings (old_id -> new_id)
        self.user_id_map: dict[int, int] = {}
        self.event_id_map: dict[int, int] = {}
        self.fish_id_map: dict[int, int] = {}
        self.team_id_map: dict[int, int] = {}
        self.enrollment_id_map: dict[int, int] = {}
        self.country_id_map: dict[int, int] = {}
        self.city_id_map: dict[int, int] = {}
        self.spot_id_map: dict[int, int] = {}
        self.sponsor_id_map: dict[int, int] = {}

        # Lookup caches
        self.event_types: dict[str, int] = {}
        self.scoring_configs: dict[str, int] = {}

    async def connect(self):
        """Connect to both databases."""
        print("\n[Connecting to databases...]")

        # Old database (asyncpg direct connection)
        print(f"  Old DB: Connecting...")
        old_url = OLD_DATABASE_URL.replace("postgresql://", "")
        self.old_conn = await asyncpg.connect(f"postgresql://{old_url}")
        print(f"  Old DB: ✓ Connected")

        # New database
        print(f"  New DB: Connecting...")
        await init_db()
        self.new_db = async_session_maker()
        print(f"  New DB: ✓ Connected")

    async def disconnect(self):
        """Close database connections."""
        if self.old_conn:
            await self.old_conn.close()
        if self.new_db:
            await self.new_db.close()

    async def load_lookups(self):
        """Load lookup tables from new database."""
        print("\n[Loading lookup tables...]")

        # Event types
        result = await self.new_db.execute(select(EventType))
        for et in result.scalars().all():
            self.event_types[et.code] = et.id
        print(f"  Event types: {list(self.event_types.keys())}")

        # Scoring configs
        result = await self.new_db.execute(select(ScoringConfig))
        for sc in result.scalars().all():
            self.scoring_configs[sc.code] = sc.id
        print(f"  Scoring configs: {list(self.scoring_configs.keys())}")

    # -------------------------------------------------------------------------
    # LOCATIONS
    # -------------------------------------------------------------------------
    async def migrate_locations(self):
        """Migrate countries, cities, and fishing spots."""
        print("\n" + "=" * 60)
        print("MIGRATING LOCATIONS")
        print("=" * 60)

        # Countries
        old_countries = await self.old_conn.fetch("SELECT * FROM location_country ORDER BY id")
        print(f"  Countries: {len(old_countries)}")

        for row in old_countries:
            row = dict(row)
            existing = await self.new_db.execute(
                select(Country).where(Country.name == row["name"])
            )
            country = existing.scalar_one_or_none()

            if not country and not self.dry_run:
                country = Country(
                    name=row["name"],
                    code=row.get("code", row["name"][:3].upper()),
                )
                self.new_db.add(country)
                await self.new_db.flush()

            if country:
                self.country_id_map[row["id"]] = country.id

        # Cities
        old_cities = await self.old_conn.fetch("SELECT * FROM location_city ORDER BY id")
        print(f"  Cities: {len(old_cities)}")

        for row in old_cities:
            row = dict(row)
            new_country_id = self.country_id_map.get(row.get("country_id"))

            if not new_country_id:
                continue

            existing = await self.new_db.execute(
                select(City).where(City.name == row["city_name"], City.country_id == new_country_id)
            )
            city = existing.scalar_one_or_none()

            if not city and not self.dry_run:
                city = City(
                    name=row["city_name"],
                    country_id=new_country_id,
                )
                self.new_db.add(city)
                await self.new_db.flush()

            if city:
                self.city_id_map[row["id"]] = city.id

        # Fishing spots
        old_spots = await self.old_conn.fetch("SELECT * FROM location_fishingspot ORDER BY id")
        print(f"  Fishing spots: {len(old_spots)}")

        for row in old_spots:
            row = dict(row)
            new_city_id = self.city_id_map.get(row.get("city_id"))

            if not new_city_id:
                continue

            existing = await self.new_db.execute(
                select(FishingSpot).where(FishingSpot.name == row["name"])
            )
            spot = existing.scalar_one_or_none()

            if not spot and not self.dry_run:
                spot = FishingSpot(
                    name=row["name"],
                    city_id=new_city_id,
                    latitude=float(row.get("latitude") or 0) if row.get("latitude") else None,
                    longitude=float(row.get("longitude") or 0) if row.get("longitude") else None,
                )
                self.new_db.add(spot)
                await self.new_db.flush()

            if spot:
                self.spot_id_map[row["id"]] = spot.id

        if not self.dry_run:
            await self.new_db.commit()

        self.stats.add("locations", len(self.country_id_map) + len(self.city_id_map) + len(self.spot_id_map))

    # -------------------------------------------------------------------------
    # FISH SPECIES
    # -------------------------------------------------------------------------
    async def migrate_fish(self):
        """Migrate fish species."""
        print("\n" + "=" * 60)
        print("MIGRATING FISH SPECIES")
        print("=" * 60)

        old_fish = await self.old_conn.fetch("SELECT * FROM fish_fish ORDER BY id")
        print(f"  Found {len(old_fish)} fish species")

        for row in old_fish:
            row = dict(row)

            # Check by name
            existing = await self.new_db.execute(
                select(Fish).where(Fish.name == row["name"])
            )
            fish = existing.scalar_one_or_none()

            if fish:
                self.fish_id_map[row["id"]] = fish.id
                print(f"    - Exists: {row['name']}")
                self.stats.add("fish", skipped=1)
            elif not self.dry_run:
                fish = Fish(
                    name=row["name"],
                    scientific_name=row.get("scientific_name", ""),
                    image_url=row.get("fish_image"),
                )
                self.new_db.add(fish)
                await self.new_db.flush()
                self.fish_id_map[row["id"]] = fish.id
                print(f"    + Created: {row['name']}")
                self.stats.add("fish", migrated=1)
            else:
                print(f"    [DRY] Would create: {row['name']}")
                self.stats.add("fish", migrated=1)

        if not self.dry_run:
            await self.new_db.commit()

    # -------------------------------------------------------------------------
    # SPONSORS
    # -------------------------------------------------------------------------
    async def migrate_sponsors(self):
        """Migrate sponsors."""
        print("\n" + "=" * 60)
        print("MIGRATING SPONSORS")
        print("=" * 60)

        old_sponsors = await self.old_conn.fetch("SELECT * FROM sponsor_sponsor ORDER BY id")
        print(f"  Found {len(old_sponsors)} sponsors")

        for row in old_sponsors:
            row = dict(row)

            existing = await self.new_db.execute(
                select(Sponsor).where(Sponsor.name == row["sponsor_name"])
            )
            sponsor = existing.scalar_one_or_none()

            if sponsor:
                self.sponsor_id_map[row["id"]] = sponsor.id
                self.stats.add("sponsors", skipped=1)
            elif not self.dry_run:
                sponsor = Sponsor(
                    name=row["sponsor_name"],
                    logo_url=row.get("sponsor_logo"),
                    website_url=row.get("sponsor_url"),
                    is_active=row.get("is_active", True),
                )
                self.new_db.add(sponsor)
                await self.new_db.flush()
                self.sponsor_id_map[row["id"]] = sponsor.id
                self.stats.add("sponsors", migrated=1)
            else:
                self.stats.add("sponsors", migrated=1)

        if not self.dry_run:
            await self.new_db.commit()

    # -------------------------------------------------------------------------
    # USERS
    # -------------------------------------------------------------------------
    async def migrate_users(self):
        """Migrate users (accounts + profiles)."""
        print("\n" + "=" * 60)
        print("MIGRATING USERS")
        print("=" * 60)

        # Join accounts with profiles
        old_users = await self.old_conn.fetch("""
            SELECT
                a.id,
                a.email,
                a.password,
                a.first_name,
                a.last_name,
                a.is_active,
                a.is_staff,
                a.is_superuser,
                a.last_login,
                p.profile_picture,
                p.phone_number,
                p.gender,
                p.is_angler,
                p.is_organiser,
                p.is_validator,
                p.is_administrator,
                p.is_sponsor,
                p.is_guest,
                p.city_id,
                p.country_id,
                p.social_media_facebook,
                p.social_media_instagram
            FROM accounts_useraccount a
            LEFT JOIN profiles_userprofile p ON p.account_id = a.id
            ORDER BY a.id
        """)

        print(f"  Found {len(old_users)} users")

        for row in old_users:
            row = dict(row)
            old_id = row["id"]

            try:
                # Check if exists by email
                existing = await self.new_db.execute(
                    select(UserAccount).where(UserAccount.email == row["email"])
                )
                user = existing.scalar_one_or_none()

                if user:
                    self.user_id_map[old_id] = user.id
                    self.stats.add("users", skipped=1)
                    continue

                if self.dry_run:
                    print(f"    [DRY] Would migrate: {row['email']}")
                    self.stats.add("users", migrated=1)
                    continue

                # Create UserAccount
                user = UserAccount(
                    email=row["email"],
                    password_hash=row["password"],  # Already hashed (Django format)
                    is_active=row.get("is_active", True),
                    is_verified=True,  # Assume verified if they exist
                    is_staff=row.get("is_staff", False),
                    is_superuser=row.get("is_superuser", False),
                )
                self.new_db.add(user)
                await self.new_db.flush()

                # Create UserProfile
                roles = map_user_roles(row)
                profile = UserProfile(
                    user_id=user.id,
                    first_name=row.get("first_name", ""),
                    last_name=row.get("last_name", ""),
                    phone_number=row.get("phone_number"),
                    profile_picture_url=row.get("profile_picture"),
                    roles=roles,
                )
                self.new_db.add(profile)

                self.user_id_map[old_id] = user.id
                self.stats.add("users", migrated=1)

                if len(self.user_id_map) % 100 == 0:
                    print(f"    Migrated {len(self.user_id_map)} users...")

            except Exception as e:
                self.stats.errors.append(f"User {old_id} ({row.get('email')}): {str(e)}")
                self.stats.add("users", skipped=1)

        if not self.dry_run:
            await self.new_db.commit()

        print(f"  ✓ Migrated {self.stats.counts.get('users', {}).get('migrated', 0)} users")

    # -------------------------------------------------------------------------
    # EVENTS
    # -------------------------------------------------------------------------
    async def migrate_events(self):
        """Migrate events."""
        print("\n" + "=" * 60)
        print("MIGRATING EVENTS")
        print("=" * 60)

        old_events = await self.old_conn.fetch("""
            SELECT * FROM competitions_event
            WHERE is_test = false
            ORDER BY id
        """)

        print(f"  Found {len(old_events)} events (excluding test events)")

        for row in old_events:
            row = dict(row)
            old_id = row["id"]

            try:
                # Map creator
                new_creator_id = self.user_id_map.get(row["created_by_id"])
                if not new_creator_id:
                    self.stats.add("events", skipped=1)
                    continue

                # Map event type and scoring config
                event_type_code = map_event_type_code(row.get("event_type_id"))
                scoring_config_code = map_scoring_config_code(
                    row.get("scoring_type_id"),
                    event_type_code
                )

                event_type_id = self.event_types.get(event_type_code)
                scoring_config_id = self.scoring_configs.get(scoring_config_code)

                if not event_type_id or not scoring_config_id:
                    self.stats.errors.append(f"Event {old_id}: Missing type/scoring config")
                    self.stats.add("events", skipped=1)
                    continue

                # Check if exists
                existing = await self.new_db.execute(
                    select(Event).where(Event.slug == row.get("slug"))
                )
                if existing.scalar_one_or_none():
                    self.stats.add("events", skipped=1)
                    continue

                if self.dry_run:
                    print(f"    [DRY] Would migrate: {row['event_name']}")
                    self.stats.add("events", migrated=1)
                    continue

                # Map location
                location_id = self.spot_id_map.get(row.get("event_location_id"))

                # Create Event
                event = Event(
                    name=row["event_name"],
                    slug=row.get("slug") or f"event-{old_id}",
                    description=row.get("details"),
                    event_type_id=event_type_id,
                    scoring_config_id=scoring_config_id,
                    start_date=row["start_date"],
                    end_date=row.get("end_date") or row["start_date"],
                    location_id=location_id,
                    location_name=row.get("event_location_details"),
                    created_by_id=new_creator_id,
                    status=map_event_status(row),
                    max_participants=row.get("max_participants"),
                    requires_approval=True,
                    image_url=row.get("event_logo"),
                    is_team_event=row.get("is_team_event", False),
                    is_national_event=row.get("is_national_event", False),
                    is_tournament_event=False,
                    has_bonus_points=row.get("has_bonus_points", True),
                    allow_gallery_upload=row.get("allow_gallery_photos", True),
                    participation_fee=Decimal(str(row.get("participation_tax", 0))) if row.get("participation_tax") else None,
                    is_deleted=row.get("is_deleted", False),
                )

                # Preserve timestamps
                if row.get("create_date"):
                    event.created_at = row["create_date"]
                if row.get("started_at"):
                    event.published_at = row["started_at"]
                if row.get("ended_at"):
                    event.completed_at = row["ended_at"]

                self.new_db.add(event)
                await self.new_db.flush()

                self.event_id_map[old_id] = event.id
                self.stats.add("events", migrated=1)

            except Exception as e:
                self.stats.errors.append(f"Event {old_id}: {str(e)}")
                self.stats.add("events", skipped=1)

        if not self.dry_run:
            await self.new_db.commit()

        print(f"  ✓ Migrated {self.stats.counts.get('events', {}).get('migrated', 0)} events")

    # -------------------------------------------------------------------------
    # EVENT FISH SCORING
    # -------------------------------------------------------------------------
    async def migrate_event_fish_scoring(self):
        """Migrate event fish scoring configurations."""
        print("\n" + "=" * 60)
        print("MIGRATING EVENT FISH SCORING")
        print("=" * 60)

        old_scoring = await self.old_conn.fetch("""
            SELECT * FROM competitions_eventfishscoring ORDER BY id
        """)

        print(f"  Found {len(old_scoring)} fish scoring configs")

        for row in old_scoring:
            row = dict(row)

            new_event_id = self.event_id_map.get(row["event_id"])
            new_fish_id = self.fish_id_map.get(row["fish_id"])

            if not new_event_id or not new_fish_id:
                continue

            if self.dry_run:
                self.stats.add("fish_scoring", migrated=1)
                continue

            try:
                scoring = EventFishScoring(
                    event_id=new_event_id,
                    fish_id=new_fish_id,
                    accountable_catch_slots=row.get("accountable_catch_slots") or 5,
                    accountable_min_length=float(row.get("accountable_min_length") or 0),
                    under_min_length_points=row.get("under_min_length_points") or 0,
                    top_x_catches=row.get("top_x_catches"),
                )
                self.new_db.add(scoring)
                self.stats.add("fish_scoring", migrated=1)
            except Exception as e:
                self.stats.errors.append(f"FishScoring {row['id']}: {str(e)}")

        if not self.dry_run:
            await self.new_db.commit()

    # -------------------------------------------------------------------------
    # EVENT SPECIES BONUS POINTS
    # -------------------------------------------------------------------------
    async def migrate_bonus_points(self):
        """Migrate event species bonus points."""
        print("\n" + "=" * 60)
        print("MIGRATING BONUS POINTS")
        print("=" * 60)

        old_bonus = await self.old_conn.fetch("""
            SELECT * FROM competitions_eventspeciesbonuspoints ORDER BY id
        """)

        print(f"  Found {len(old_bonus)} bonus point configs")

        for row in old_bonus:
            row = dict(row)

            new_event_id = self.event_id_map.get(row["event_id"])
            if not new_event_id:
                continue

            if self.dry_run:
                self.stats.add("bonus_points", migrated=1)
                continue

            try:
                bonus = EventSpeciesBonusPoints(
                    event_id=new_event_id,
                    species_count=row["species_count"],
                    bonus_points=row["bonus_points"],
                )
                self.new_db.add(bonus)
                self.stats.add("bonus_points", migrated=1)
            except Exception as e:
                self.stats.errors.append(f"BonusPoints {row['id']}: {str(e)}")

        if not self.dry_run:
            await self.new_db.commit()

    # -------------------------------------------------------------------------
    # ENROLLMENTS
    # -------------------------------------------------------------------------
    async def migrate_enrollments(self):
        """Migrate event enrollments."""
        print("\n" + "=" * 60)
        print("MIGRATING ENROLLMENTS")
        print("=" * 60)

        old_enrollments = await self.old_conn.fetch("""
            SELECT * FROM enrolment_eventenrolment ORDER BY id
        """)

        print(f"  Found {len(old_enrollments)} enrollments")

        for row in old_enrollments:
            row = dict(row)
            old_id = row["id"]

            new_user_id = self.user_id_map.get(row["user_id"])
            new_event_id = self.event_id_map.get(row["event_id"])

            if not new_user_id or not new_event_id:
                self.stats.add("enrollments", skipped=1)
                continue

            if self.dry_run:
                self.stats.add("enrollments", migrated=1)
                continue

            try:
                enrollment = EventEnrollment(
                    user_id=new_user_id,
                    event_id=new_event_id,
                    status=map_enrollment_status(
                        row.get("enrollment_status", ""),
                        row.get("is_approved", False)
                    ),
                    enrollment_number=row.get("enrollment_number"),
                )

                if row.get("enrollment_date"):
                    enrollment.created_at = row["enrollment_date"]
                if row.get("approved_date"):
                    enrollment.approved_at = row["approved_date"]

                # Map approver
                if row.get("approved_by_id"):
                    approver_id = self.user_id_map.get(row["approved_by_id"])
                    if approver_id:
                        enrollment.approved_by_id = approver_id

                self.new_db.add(enrollment)
                await self.new_db.flush()

                self.enrollment_id_map[old_id] = enrollment.id
                self.stats.add("enrollments", migrated=1)

            except Exception as e:
                self.stats.errors.append(f"Enrollment {old_id}: {str(e)}")
                self.stats.add("enrollments", skipped=1)

        if not self.dry_run:
            await self.new_db.commit()

        print(f"  ✓ Migrated {self.stats.counts.get('enrollments', {}).get('migrated', 0)} enrollments")

    # -------------------------------------------------------------------------
    # TEAMS
    # -------------------------------------------------------------------------
    async def migrate_teams(self):
        """Migrate teams."""
        print("\n" + "=" * 60)
        print("MIGRATING TEAMS")
        print("=" * 60)

        old_teams = await self.old_conn.fetch("SELECT * FROM teams_team ORDER BY id")
        print(f"  Found {len(old_teams)} teams")

        for row in old_teams:
            row = dict(row)
            old_id = row["id"]

            new_event_id = self.event_id_map.get(row["event_id"])
            new_creator_id = self.user_id_map.get(row["created_by_id"])

            if not new_event_id:
                self.stats.add("teams", skipped=1)
                continue

            if self.dry_run:
                self.stats.add("teams", migrated=1)
                continue

            try:
                team = Team(
                    name=row["name"],
                    event_id=new_event_id,
                    team_number=row.get("team_number"),
                    created_by_id=new_creator_id,
                )

                if row.get("created_at"):
                    team.created_at = row["created_at"]

                self.new_db.add(team)
                await self.new_db.flush()

                self.team_id_map[old_id] = team.id
                self.stats.add("teams", migrated=1)

            except Exception as e:
                self.stats.errors.append(f"Team {old_id}: {str(e)}")
                self.stats.add("teams", skipped=1)

        if not self.dry_run:
            await self.new_db.commit()

        # Migrate team members
        old_members = await self.old_conn.fetch("SELECT * FROM teams_teammember ORDER BY id")
        print(f"  Found {len(old_members)} team members")

        for row in old_members:
            row = dict(row)

            new_team_id = self.team_id_map.get(row["team_id"])
            new_enrollment_id = self.enrollment_id_map.get(row.get("enrollment_id"))

            if not new_team_id or not new_enrollment_id:
                continue

            if self.dry_run:
                continue

            try:
                member = TeamMember(
                    team_id=new_team_id,
                    enrollment_id=new_enrollment_id,
                    role=row.get("role", "member"),
                    is_active=row.get("is_active", True),
                )
                self.new_db.add(member)
            except Exception as e:
                self.stats.errors.append(f"TeamMember: {str(e)}")

        if not self.dry_run:
            await self.new_db.commit()

    # -------------------------------------------------------------------------
    # CATCHES
    # -------------------------------------------------------------------------
    async def migrate_catches(self):
        """Migrate catches (photo captures)."""
        print("\n" + "=" * 60)
        print("MIGRATING CATCHES")
        print("=" * 60)

        old_catches = await self.old_conn.fetch("""
            SELECT * FROM competitions_eventphotocaptures ORDER BY id
        """)

        print(f"  Found {len(old_catches)} catches")

        batch_size = 500
        batch = []

        for i, row in enumerate(old_catches):
            row = dict(row)
            old_id = row["id"]

            new_user_id = self.user_id_map.get(row["user_id"])
            new_event_id = self.event_id_map.get(row["event_id"])
            new_fish_id = self.fish_id_map.get(row["fish_id"])

            if not new_user_id or not new_event_id or not new_fish_id:
                self.stats.add("catches", skipped=1)
                continue

            if self.dry_run:
                self.stats.add("catches", migrated=1)
                continue

            try:
                catch = Catch(
                    user_id=new_user_id,
                    event_id=new_event_id,
                    fish_id=new_fish_id,
                    length=float(row.get("fish_length", 0)),
                    photo_url=row.get("photo"),
                    status=map_catch_status(
                        row.get("is_approved", False),
                        row.get("is_rejected", False)
                    ),
                    rejection_reason=row.get("rejected_reason"),
                    latitude=float(row["latitude"]) if row.get("latitude") else None,
                    longitude=float(row["longitude"]) if row.get("longitude") else None,
                )

                # Timestamps
                if row.get("uploaded_at"):
                    catch.submitted_at = row["uploaded_at"]

                if row.get("approved_date"):
                    catch.validated_at = row["approved_date"]
                elif row.get("rejected_date"):
                    catch.validated_at = row["rejected_date"]

                # Validator
                validator_id = row.get("approved_by_id") or row.get("rejected_by_id")
                if validator_id:
                    new_validator_id = self.user_id_map.get(validator_id)
                    if new_validator_id:
                        catch.validated_by_id = new_validator_id

                batch.append(catch)
                self.stats.add("catches", migrated=1)

                # Commit in batches
                if len(batch) >= batch_size:
                    self.new_db.add_all(batch)
                    await self.new_db.commit()
                    print(f"    Migrated {i + 1} catches...")
                    batch = []

            except Exception as e:
                self.stats.errors.append(f"Catch {old_id}: {str(e)}")
                self.stats.add("catches", skipped=1)

        # Commit remaining
        if batch and not self.dry_run:
            self.new_db.add_all(batch)
            await self.new_db.commit()

        print(f"  ✓ Migrated {self.stats.counts.get('catches', {}).get('migrated', 0)} catches")

    # -------------------------------------------------------------------------
    # RUN MIGRATION
    # -------------------------------------------------------------------------
    async def run(self, entities: Optional[list[str]] = None):
        """Run the full migration."""
        try:
            await self.connect()
            await self.load_lookups()

            all_entities = [
                "locations", "fish", "sponsors", "users",
                "events", "fish_scoring", "bonus_points",
                "enrollments", "teams", "catches"
            ]
            to_migrate = entities or all_entities

            if "locations" in to_migrate:
                await self.migrate_locations()

            if "fish" in to_migrate:
                await self.migrate_fish()

            if "sponsors" in to_migrate:
                await self.migrate_sponsors()

            if "users" in to_migrate:
                await self.migrate_users()

            if "events" in to_migrate:
                await self.migrate_events()

            if "fish_scoring" in to_migrate:
                await self.migrate_event_fish_scoring()

            if "bonus_points" in to_migrate:
                await self.migrate_bonus_points()

            if "enrollments" in to_migrate:
                await self.migrate_enrollments()

            if "teams" in to_migrate:
                await self.migrate_teams()

            if "catches" in to_migrate:
                await self.migrate_catches()

            self.stats.print_summary()

            if self.dry_run:
                print("\n⚠️  DRY RUN - No changes were made to the database")
            else:
                print("\n✅ Migration completed!")

        finally:
            await self.disconnect()


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Migrate data from old Reelin to new Reelin V2")
    parser.add_argument("--dry-run", action="store_true", help="Run without making changes")
    parser.add_argument("--only", type=str, help="Migrate only specific entities (comma-separated)")
    args = parser.parse_args()

    entities = None
    if args.only:
        entities = [e.strip() for e in args.only.split(",")]

    print("=" * 60)
    print("Reelin Data Migration: Django → FastAPI")
    print("=" * 60)
    print(f"Old DB: {OLD_DATABASE_URL.split('@')[1].split('/')[0] if '@' in OLD_DATABASE_URL else 'configured'}")

    if args.dry_run:
        print("MODE: DRY RUN (no changes will be made)")
    else:
        print("MODE: LIVE MIGRATION")
        print("\n⚠️  WARNING: This will modify the production database!")
        confirm = input("Type 'MIGRATE' to continue: ")
        if confirm != "MIGRATE":
            print("Aborted.")
            return

    migrator = DataMigrator(dry_run=args.dry_run)
    asyncio.run(migrator.run(entities))


if __name__ == "__main__":
    main()
