"""
Create organizer_rules and organizer_rule_defaults tables.
Also adds rule_id column to events table.

Run with: python -m scripts.create_rules_tables
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.database import engine


async def create_tables():
    """Create the rules tables and update events table."""

    async with engine.begin() as conn:
        # Check if organizer_rules table exists
        result = await conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'organizer_rules'
            );
        """))
        table_exists = result.scalar()

        if table_exists:
            print("organizer_rules table already exists")
        else:
            print("Creating organizer_rules table...")
            await conn.execute(text("""
                CREATE TABLE organizer_rules (
                    id SERIAL PRIMARY KEY,
                    owner_id INTEGER NOT NULL REFERENCES user_accounts(id) ON DELETE CASCADE,
                    name VARCHAR(100) NOT NULL,
                    description VARCHAR(255),
                    content TEXT,
                    external_url VARCHAR(500),
                    is_active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE INDEX idx_organizer_rules_owner ON organizer_rules(owner_id);
            """))
            print("organizer_rules table created successfully")

        # Check if organizer_rule_defaults table exists
        result = await conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'organizer_rule_defaults'
            );
        """))
        table_exists = result.scalar()

        if table_exists:
            print("organizer_rule_defaults table already exists")
        else:
            print("Creating organizer_rule_defaults table...")
            await conn.execute(text("""
                CREATE TABLE organizer_rule_defaults (
                    id SERIAL PRIMARY KEY,
                    owner_id INTEGER NOT NULL REFERENCES user_accounts(id) ON DELETE CASCADE,
                    event_type_id INTEGER NOT NULL REFERENCES event_types(id) ON DELETE CASCADE,
                    rule_id INTEGER NOT NULL REFERENCES organizer_rules(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT uq_organizer_rule_default UNIQUE (owner_id, event_type_id)
                );

                CREATE INDEX idx_organizer_rule_defaults_owner ON organizer_rule_defaults(owner_id);
                CREATE INDEX idx_organizer_rule_defaults_event_type ON organizer_rule_defaults(event_type_id);
                CREATE INDEX idx_organizer_rule_defaults_rule ON organizer_rule_defaults(rule_id);
            """))
            print("organizer_rule_defaults table created successfully")

        # Check if rule_id column exists in events table
        result = await conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.columns
                WHERE table_name = 'events' AND column_name = 'rule_id'
            );
        """))
        column_exists = result.scalar()

        if column_exists:
            print("rule_id column already exists in events table")
        else:
            print("Adding rule_id column to events table...")
            await conn.execute(text("""
                ALTER TABLE events
                ADD COLUMN rule_id INTEGER REFERENCES organizer_rules(id) ON DELETE SET NULL
            """))
            await conn.execute(text("""
                CREATE INDEX idx_events_rule ON events(rule_id)
            """))
            print("rule_id column added to events table")

        print("\nAll rules tables and columns created successfully!")


if __name__ == "__main__":
    asyncio.run(create_tables())
