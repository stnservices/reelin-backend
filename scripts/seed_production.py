"""
Production seed script for ReelIn application.

Seeds ONLY essential reference data required for the app to function:
- Event Types
- Scoring Configurations (CRITICAL for leaderboard calculations)
- App Settings
- Pro Settings (subscription display prices)
- Initial admin user

Run with: python -m scripts.seed_production
Or from Docker: docker-compose exec backend python -m scripts.seed_production

NOTE: Fish species and currencies are seeded via migrations.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker, init_db
from app.core.security import get_password_hash
from app.models.user import UserAccount, UserProfile
from app.models.event import EventType, ScoringConfig
from app.models.app_settings import AppSettings
from app.models.pro import ProSettings
from app.models.trout_area import TAPointsRule


# =============================================================================
# EVENT TYPES
# =============================================================================
# These define the categories of fishing competitions supported by the platform.
# Each event type has specific scoring configurations available.

EVENT_TYPES = [
    {
        "name": "Street Fishing",
        "code": "street_fishing",
        "description": "Urban fishing competitions in city waters. Uses length-based scoring with species diversity bonuses.",
        "is_active": True,
    },
    {
        "name": "Trout Area",
        "code": "trout_area",
        "description": "Trout fishing in designated managed areas. Uses match-based head-to-head scoring.",
        "is_active": True,
    },
    {
        "name": "Trout Shore Fishing",
        "code": "trout_shore",
        "description": "Shore-based trout fishing competitions. Uses group stage with finals format.",
        "is_active": True,
    },
]


# =============================================================================
# SCORING CONFIGURATIONS
# =============================================================================
# CRITICAL: These configurations drive the leaderboard calculation logic.
# The `code` field determines which scoring algorithm is used in:
# app/tasks/leaderboard.py :: _calculate_catch_details()
#
# Two main scoring methods:
# 1. top_x_overall - Global top N catches, regardless of species
# 2. top_x_by_species - Top N catches per species (slot-based)
#
# The `rules` JSONB field stores additional parameters for the scoring engine.

SCORING_CONFIGS = [
    # -------------------------------------------------------------------------
    # Street Fishing Scoring Types
    # -------------------------------------------------------------------------
    {
        "name": "Top X Catches (Overall)",
        "code": "sf_top_x_overall",
        "description": """
Score based on the top X catches by length, regardless of species.
Each catch's points = length in cm (or under_min_length_points if below minimum).
Only the top X catches count toward the total score.

6-Level Tiebreaker:
1. Total Points (sum of top X catch lengths)
2. Counted Catches (more catches = better)
3. Species Count (more species = better)
4. Best Single Catch Length
5. Average Catch Length
6. First Catch Time (earlier = better)

Example: top_x_overall=10 means only the 10 longest catches count.
""".strip(),
        "default_top_x": 10,
        "default_catch_slots": 10,  # Not used for overall, but kept for consistency
        "rules": {
            "scoring_method": "top_n_overall",
            "measure": "length",
            "tie_breaker_order": [
                "total_points",
                "counted_catches",
                "species_count",
                "best_catch_length",
                "average_catch",
                "first_catch_time"
            ],
        },
        "event_types": ["street_fishing"],
    },
    {
        "name": "Top X by Species (Slot-Based)",
        "code": "sf_top_x_by_species",
        "description": """
Score based on top X catches PER SPECIES. Each species has its own slot limit.
Points = length in cm for each catch within the slot limit.

Species slots are configured per event via EventFishScoring:
- accountable_catch_slots: How many catches of this species count
- accountable_min_length: Minimum length for full points
- under_min_length_points: Points for catches below minimum

Example: If Pike has 5 slots, only your 5 longest Pike catches count.

6-Level Tiebreaker (same as overall):
1. Total Points
2. Counted Catches
3. Species Count
4. Best Single Catch Length
5. Average Catch Length
6. First Catch Time
""".strip(),
        "default_top_x": 5,
        "default_catch_slots": 5,
        "rules": {
            "scoring_method": "top_n_by_species",
            "measure": "length",
            "species_slots": True,
            "tie_breaker_order": [
                "total_points",
                "counted_catches",
                "species_count",
                "best_catch_length",
                "average_catch",
                "first_catch_time"
            ],
        },
        "event_types": ["street_fishing"],
    },
    # -------------------------------------------------------------------------
    # Trout Area Scoring Types - Match-Based Head-to-Head
    # -------------------------------------------------------------------------
    {
        "name": "TA Qualifier + Knockout",
        "code": "ta_qualifier_knockout",
        "description": """
Trout Area head-to-head match scoring with qualifier and knockout phases.

QUALIFIER PHASE:
- Competitors paired by draw number (seats 1-2, 3-4, 5-6...)
- Each leg: seats rotate according to pattern
- Self-validation: both competitors validate catches
- Match winner: most fish caught

POINT RULES:
- Victory (V): 3.0 points
- Tie with fish (T): 1.5 points each
- Tie no fish (T0): 1.0 points each
- Loss with fish (L): 0.5 points
- Loss no fish (L0): 0.0 points

KNOCKOUT PHASE:
- Top qualifiers advance to bracket
- Requalification for 2nd chance
- Semifinals (MECI 1 & 2)
- Finals: Finala Mare (1st/2nd), Finala Mica (3rd/4th)

Tiebreakers: Total Points → Victories → Fish Caught → Head-to-Head
""".strip(),
        "default_top_x": 5,
        "default_catch_slots": 1,
        "rules": {
            "scoring_type": "match_bracket",
            "phases": ["qualifier", "requalification", "semifinal", "final_grand", "final_small"],
            "points": {
                "V": 3.0,
                "T": 1.5,
                "T0": 1.0,
                "L": 0.5,
                "L0": 0.0,
            },
            "qualifier_settings": {
                "default_legs": 5,
                "rounds_per_leg": 1,
            },
            "knockout_settings": {
                "qualifiers_to_bracket": 6,
                "has_requalification": True,
                "direct_placement_from": 7,
            },
            "tiebreakers": ["total_points", "total_victories", "total_fish", "head_to_head"],
            "validation": "self",  # Competitors validate between themselves
        },
        "event_types": ["trout_area"],
    },
    {
        "name": "TA Team Match Format",
        "code": "ta_team_match",
        "description": """
Team-based Trout Area competition.
Teams of 2-4 compete in parallel matches.
Team score = sum of individual member points.
""".strip(),
        "default_top_x": 5,
        "default_catch_slots": 1,
        "rules": {
            "scoring_type": "match_bracket",
            "is_team_event": True,
            "team_size_min": 2,
            "team_size_max": 4,
            "team_scoring_method": "sum",  # sum, average, best_n
            "points": {
                "V": 3.0,
                "T": 1.5,
                "T0": 1.0,
                "L": 0.5,
                "L0": 0.0,
            },
            "validation": "self",
        },
        "event_types": ["trout_area"],
    },
    # -------------------------------------------------------------------------
    # Trout Shore Fishing - Multi-Day Positional Scoring
    # -------------------------------------------------------------------------
    {
        "name": "TSF Multi-Day Sectors",
        "code": "tsf_multi_day",
        "description": """
Trout Shore Fishing multi-day positional scoring.

STRUCTURE:
- Competition spans multiple days (usually 2, configurable)
- Participants divided into sectors/groups
- Multiple legs per day (typically 4)

SCORING (Position-Based, Lower = Better):
- 1st place: 1 point
- 2nd place: 2 points
- 3rd place: 3 points
- N-th place: N points

RANKINGS:
- Daily ranking per sector
- Overall ranking across all days
- Final standing = sum of all position points

TIEBREAKERS:
1. Total Position Points (lower wins)
2. Number of 1st Places
3. Number of 2nd Places
4. Best Single Leg
""".strip(),
        "default_top_x": 4,
        "default_catch_slots": 4,
        "rules": {
            "scoring_type": "position",
            "scoring_direction": "lower",  # lower is better
            "default_days": 2,
            "default_legs_per_day": 4,
            "default_sectors": 4,
            "tiebreakers": ["total_position_points", "first_places", "second_places", "best_single_leg"],
        },
        "event_types": ["trout_shore"],
    },
]


# =============================================================================
# TA POINTS RULES
# =============================================================================
# Default point values for TA match outcomes.

TA_POINTS_RULES = [
    {
        "code": "V",
        "points": "3.00",
        "label": "Victory",
        "description": "Won the head-to-head match (more fish caught)",
    },
    {
        "code": "T",
        "points": "1.50",
        "label": "Tie with fish",
        "description": "Both caught same number of fish (at least 1)",
    },
    {
        "code": "T0",
        "points": "1.00",
        "label": "Tie no fish",
        "description": "Neither competitor caught any fish",
    },
    {
        "code": "L",
        "points": "0.50",
        "label": "Loss with fish",
        "description": "Lost but caught at least 1 fish",
    },
    {
        "code": "L0",
        "points": "0.00",
        "label": "Loss no fish",
        "description": "Lost and caught no fish",
    },
]


# =============================================================================
# APP SETTINGS
# =============================================================================
# Mobile app version requirements and store links.

APP_SETTINGS = {
    "app_version": "1.0.0",
    "app_min_version_ios": "1.0.0",
    "app_min_version_android": "1.0.0",
    "app_store_url": "https://apps.apple.com/app/reelin/id123456789",
    "play_store_url": "https://play.google.com/store/apps/details?id=ro.reelin.app",
    "release_notes": "Initial release",
    "force_update_message": None,
}


# =============================================================================
# PRO SETTINGS
# =============================================================================
# Display prices and feature flags for Pro subscription.
# NOTE: These are for display purposes only. Actual billing is handled by Stripe.
# The Stripe price IDs in the backend configuration determine actual charges.

PRO_SETTINGS = [
    {
        "key": "monthly_price",
        "value": "4.99",
        "description": "Monthly subscription price displayed in app (EUR)",
    },
    {
        "key": "yearly_price",
        "value": "29.99",
        "description": "Yearly subscription price displayed in app (EUR)",
    },
    {
        "key": "currency",
        "value": "EUR",
        "description": "Currency code for price display",
    },
    {
        "key": "yearly_savings_percent",
        "value": "50",
        "description": "Savings percentage shown for yearly plan (vs monthly)",
    },
    {
        "key": "trial_days",
        "value": "7",
        "description": "Free trial period in days (0 to disable)",
    },
    {
        "key": "pro_features",
        "value": "advanced_stats,export_data,priority_support,no_ads,custom_notifications",
        "description": "Comma-separated list of Pro feature keys",
    },
]


# =============================================================================
# SEED FUNCTIONS
# =============================================================================

async def seed_event_types(db: AsyncSession) -> dict[str, int]:
    """Seed event types."""
    print("\n[Event Types]")

    type_ids = {}
    for data in EVENT_TYPES:
        result = await db.execute(select(EventType).where(EventType.code == data["code"]))
        event_type = result.scalar_one_or_none()

        if not event_type:
            event_type = EventType(**data)
            db.add(event_type)
            await db.flush()
            print(f"  + Created: {data['name']} ({data['code']})")
        else:
            print(f"  - Exists: {data['name']} ({data['code']})")

        type_ids[data["code"]] = event_type.id

    await db.commit()
    return type_ids


async def seed_scoring_configs(db: AsyncSession, type_ids: dict[str, int]) -> None:
    """Seed scoring configurations with event type associations."""
    print("\n[Scoring Configurations]")

    # Get event type objects for M2M relationships
    event_type_objects = {}
    for code in type_ids.keys():
        result = await db.execute(select(EventType).where(EventType.code == code))
        event_type_objects[code] = result.scalar_one()

    for data in SCORING_CONFIGS:
        result = await db.execute(
            select(ScoringConfig).where(ScoringConfig.code == data["code"])
        )
        existing = result.scalar_one_or_none()

        if not existing:
            # Extract event_types for M2M
            event_type_codes = data.pop("event_types", [])

            config = ScoringConfig(
                name=data["name"],
                code=data["code"],
                description=data["description"],
                default_top_x=data.get("default_top_x", 10),
                default_catch_slots=data.get("default_catch_slots", 5),
                rules=data.get("rules", {}),
                is_active=True,
            )

            # Assign event types
            for et_code in event_type_codes:
                if et_code in event_type_objects:
                    config.event_types.append(event_type_objects[et_code])

            db.add(config)
            print(f"  + Created: {data['name']} ({data['code']})")
            print(f"    → Event Types: {', '.join(event_type_codes)}")
            print(f"    → Scoring Method: {data.get('rules', {}).get('scoring_method', data.get('rules', {}).get('scoring_type', 'N/A'))}")
        else:
            print(f"  - Exists: {data['name']} ({data['code']})")
            # Restore event_types key for next iteration
            data["event_types"] = [et.code for et in existing.event_types]

    await db.commit()


async def seed_ta_points_rules(db: AsyncSession) -> None:
    """Seed TA (Trout Area) match outcome point rules."""
    print("\n[TA Points Rules]")

    for data in TA_POINTS_RULES:
        result = await db.execute(
            select(TAPointsRule).where(TAPointsRule.code == data["code"])
        )
        existing = result.scalar_one_or_none()

        if not existing:
            from decimal import Decimal
            rule = TAPointsRule(
                code=data["code"],
                points=Decimal(data["points"]),
                label=data["label"],
                description=data.get("description"),
                is_active=True,
            )
            db.add(rule)
            print(f"  + Created: {data['code']} = {data['points']} pts ({data['label']})")
        else:
            print(f"  - Exists: {data['code']} = {existing.points} pts")

    await db.commit()


async def seed_app_settings(db: AsyncSession) -> None:
    """Seed app settings (single row table)."""
    print("\n[App Settings]")

    result = await db.execute(select(AppSettings).where(AppSettings.id == 1))
    settings = result.scalar_one_or_none()

    if not settings:
        settings = AppSettings(id=1, **APP_SETTINGS)
        db.add(settings)
        await db.commit()
        print(f"  + Created app settings (version: {APP_SETTINGS['app_version']})")
    else:
        print(f"  - Exists (version: {settings.app_version})")


async def seed_pro_settings(db: AsyncSession) -> None:
    """Seed Pro subscription settings."""
    print("\n[Pro Settings]")

    for data in PRO_SETTINGS:
        result = await db.execute(
            select(ProSettings).where(ProSettings.key == data["key"])
        )
        existing = result.scalar_one_or_none()

        if not existing:
            setting = ProSettings(**data)
            db.add(setting)
            print(f"  + Created: {data['key']} = {data['value']}")
        else:
            print(f"  - Exists: {data['key']} = {existing.value}")

    await db.commit()


async def seed_admin_user(db: AsyncSession) -> None:
    """Create initial admin user."""
    print("\n[Admin User]")

    admin_email = os.getenv("ADMIN_EMAIL", "admin@reelin.ro")
    admin_password = os.getenv("ADMIN_PASSWORD", None)

    if not admin_password:
        print("  ! WARNING: ADMIN_PASSWORD env var not set")
        print("    Using default password - CHANGE THIS IN PRODUCTION!")
        admin_password = "ReelIn2024Admin!"

    result = await db.execute(
        select(UserAccount).where(UserAccount.email == admin_email)
    )
    existing = result.scalar_one_or_none()

    if existing:
        print(f"  - Exists: {admin_email}")
        return

    admin = UserAccount(
        email=admin_email,
        password_hash=get_password_hash(admin_password),
        is_active=True,
        is_verified=True,
        is_staff=True,
        is_superuser=True,
    )
    db.add(admin)
    await db.flush()

    profile = UserProfile(
        user_id=admin.id,
        first_name="System",
        last_name="Administrator",
        roles=["administrator", "organizer", "validator"],
    )
    db.add(profile)
    await db.commit()

    print(f"  + Created: {admin_email}")
    if os.getenv("ADMIN_PASSWORD"):
        print("    Password set from ADMIN_PASSWORD env var")
    else:
        print(f"    Default password: {admin_password}")


# =============================================================================
# MAIN
# =============================================================================

async def main():
    """Run production seed."""
    print("=" * 60)
    print("ReelIn Production Seed")
    print("=" * 60)

    # Initialize database
    await init_db()

    async with async_session_maker() as db:
        # 1. Event Types
        type_ids = await seed_event_types(db)

        # 2. Scoring Configurations (depends on event types)
        await seed_scoring_configs(db, type_ids)

        # 3. TA Points Rules (for Trout Area match scoring)
        await seed_ta_points_rules(db)

        # 4. App Settings
        await seed_app_settings(db)

        # 5. Pro Settings
        await seed_pro_settings(db)

        # 6. Admin User
        await seed_admin_user(db)

    print("\n" + "=" * 60)
    print("Production seed completed!")
    print("=" * 60)

    print("\n📋 Summary:")
    print(f"   Event Types: {len(EVENT_TYPES)}")
    print(f"   Scoring Configs: {len(SCORING_CONFIGS)}")
    print(f"   TA Points Rules: {len(TA_POINTS_RULES)}")
    print(f"   Pro Settings: {len(PRO_SETTINGS)}")
    print("\n⚠️  Remember:")
    print("   - Fish species and currencies are seeded via migrations")
    print("   - Update ADMIN_PASSWORD in production")
    print("   - Configure Stripe price IDs in environment")
    print("   - TA/TSF tables require migration before seeding")


if __name__ == "__main__":
    asyncio.run(main())
