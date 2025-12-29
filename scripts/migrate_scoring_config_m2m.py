"""
Migration script to convert ScoringConfig from one-to-many to many-to-many with EventType.

Changes:
1. Creates scoring_config_event_types association table
2. Migrates existing event_type_id relationships to the new M2M table
3. Drops the event_type_id column from scoring_configs

Run with: python -m scripts.migrate_scoring_config_m2m
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.database import async_session_maker, engine


async def migrate():
    """Migrate ScoringConfig to many-to-many relationship with EventType."""
    print("=" * 60)
    print("Migrating ScoringConfig to Many-to-Many with EventType")
    print("=" * 60)

    async with engine.begin() as conn:
        # Step 1: Create the association table if it doesn't exist
        print("\n1. Creating association table...")
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS scoring_config_event_types (
                scoring_config_id INTEGER NOT NULL REFERENCES scoring_configs(id) ON DELETE CASCADE,
                event_type_id INTEGER NOT NULL REFERENCES event_types(id) ON DELETE CASCADE,
                PRIMARY KEY (scoring_config_id, event_type_id)
            )
        """))
        print("   Created scoring_config_event_types table")

        # Step 2: Check if event_type_id column exists in scoring_configs
        result = await conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'scoring_configs' AND column_name = 'event_type_id'
        """))
        has_event_type_id = result.fetchone() is not None

        if has_event_type_id:
            # Step 3: Migrate existing relationships to the association table
            print("\n2. Migrating existing relationships...")
            result = await conn.execute(text("""
                INSERT INTO scoring_config_event_types (scoring_config_id, event_type_id)
                SELECT id, event_type_id FROM scoring_configs
                WHERE event_type_id IS NOT NULL
                ON CONFLICT DO NOTHING
            """))
            print(f"   Migrated {result.rowcount} relationships")

            # Step 4: Drop the event_type_id column
            print("\n3. Dropping event_type_id column...")
            await conn.execute(text("""
                ALTER TABLE scoring_configs DROP COLUMN IF EXISTS event_type_id
            """))
            print("   Dropped event_type_id column")
        else:
            print("\n2. event_type_id column not found - already migrated or fresh schema")

        # Step 5: Add unique constraint on code if not exists
        print("\n4. Ensuring unique constraint on code...")
        try:
            await conn.execute(text("""
                ALTER TABLE scoring_configs ADD CONSTRAINT scoring_configs_code_key UNIQUE (code)
            """))
            print("   Added unique constraint on code")
        except Exception:
            print("   Unique constraint already exists")

    # Verify
    print("\n" + "=" * 60)
    print("Verification:")
    async with async_session_maker() as db:
        result = await db.execute(text("""
            SELECT sc.id, sc.code, sc.name, array_agg(et.code) as event_types
            FROM scoring_configs sc
            LEFT JOIN scoring_config_event_types scet ON sc.id = scet.scoring_config_id
            LEFT JOIN event_types et ON scet.event_type_id = et.id
            GROUP BY sc.id, sc.code, sc.name
            ORDER BY sc.name
        """))
        rows = result.fetchall()
        for row in rows:
            event_types = [et for et in (row[3] or []) if et]
            print(f"  - {row[1]} ({row[2]}): {event_types or 'no event types'}")

    print("\n" + "=" * 60)
    print("Migration completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(migrate())
