"""
Migration script to drop the scoring_method column from scoring_configs table.

The scoring_method column is no longer needed - the scoring type is determined
by the `code` field on ScoringConfig (e.g., 'top_x_by_species', 'top_x_overall').

Run with: python -m scripts.migrate_drop_scoring_method
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.database import async_session_maker, engine


async def migrate():
    """Drop the scoring_method column from scoring_configs."""
    print("=" * 60)
    print("Dropping scoring_method column from scoring_configs")
    print("=" * 60)

    async with engine.begin() as conn:
        # Check if scoring_method column exists
        result = await conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'scoring_configs' AND column_name = 'scoring_method'
        """))
        has_column = result.fetchone() is not None

        if has_column:
            print("\n1. Dropping scoring_method column...")
            await conn.execute(text("""
                ALTER TABLE scoring_configs DROP COLUMN IF EXISTS scoring_method
            """))
            print("   Dropped scoring_method column")
        else:
            print("\n1. scoring_method column not found - already dropped or fresh schema")

    # Verify current schema
    print("\n" + "=" * 60)
    print("Verification - Current scoring_configs columns:")
    async with async_session_maker() as db:
        result = await db.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'scoring_configs'
            ORDER BY ordinal_position
        """))
        rows = result.fetchall()
        for row in rows:
            print(f"  - {row[0]}: {row[1]}")

    print("\n" + "=" * 60)
    print("Current scoring configs:")
    async with async_session_maker() as db:
        result = await db.execute(text("""
            SELECT id, code, name, is_active
            FROM scoring_configs
            ORDER BY name
        """))
        rows = result.fetchall()
        for row in rows:
            status = "active" if row[3] else "inactive"
            print(f"  - [{row[0]}] {row[1]}: {row[2]} ({status})")

    print("\n" + "=" * 60)
    print("Migration completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(migrate())
