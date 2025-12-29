"""
Migration script to rename StreetFishing scoring configs.

Changes:
- sf_top_5 → sf_top_x_overall
- sf_top_5_species → sf_top_x_by_species
- sf_all → deactivated (is_active = false)

Run with: python -m scripts.migrate_sf_scoring_configs
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, update

from app.database import async_session_maker, init_db
from app.models.event import ScoringConfig


async def migrate():
    """Migrate StreetFishing scoring configs."""
    print("=" * 50)
    print("Migrating StreetFishing Scoring Configs")
    print("=" * 50)

    async with async_session_maker() as db:
        # 1. Rename sf_top_5 → sf_top_x_overall
        result = await db.execute(
            update(ScoringConfig)
            .where(ScoringConfig.code == "sf_top_5")
            .values(
                code="sf_top_x_overall",
                name="Top X Catches",
                description="Score based on top X catches by length, regardless of species",
            )
        )
        print(f"  Renamed sf_top_5 → sf_top_x_overall ({result.rowcount} rows)")

        # 2. Rename sf_top_5_species → sf_top_x_by_species
        result = await db.execute(
            update(ScoringConfig)
            .where(ScoringConfig.code == "sf_top_5_species")
            .values(
                code="sf_top_x_by_species",
                name="Top X by Species",
                description="Score based on top X catches per species slot",
            )
        )
        print(f"  Renamed sf_top_5_species → sf_top_x_by_species ({result.rowcount} rows)")

        # 3. Deactivate sf_all
        result = await db.execute(
            update(ScoringConfig)
            .where(ScoringConfig.code == "sf_all")
            .values(is_active=False)
        )
        print(f"  Deactivated sf_all ({result.rowcount} rows)")

        await db.commit()

        # Verify
        print("\nVerification:")
        result = await db.execute(
            select(ScoringConfig).where(
                ScoringConfig.code.in_(["sf_top_x_overall", "sf_top_x_by_species", "sf_all"])
            )
        )
        configs = result.scalars().all()
        for config in configs:
            status = "active" if config.is_active else "DEACTIVATED"
            print(f"  - {config.code}: {config.name} [{status}]")

    print("\n" + "=" * 50)
    print("Migration completed successfully!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(migrate())
