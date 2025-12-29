"""
Add event_bans table for banning users from events.

Run with: python -m scripts.add_event_bans_table
Or from Docker: docker-compose exec backend python -m scripts.add_event_bans_table
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.database import async_session_maker, init_db


async def create_event_bans_table():
    """Create the event_bans table if it doesn't exist."""
    print("Creating event_bans table...")

    await init_db()

    async with async_session_maker() as db:
        # Check if table already exists
        check_query = text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'event_bans'
            )
        """)
        result = await db.execute(check_query)
        exists = result.scalar()

        if exists:
            print("  Table event_bans already exists")
            return

        # Create the table
        create_query = text("""
            CREATE TABLE event_bans (
                id SERIAL PRIMARY KEY,
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES user_accounts(id) ON DELETE CASCADE,
                banned_by_id INTEGER REFERENCES user_accounts(id) ON DELETE SET NULL,
                reason VARCHAR(500),
                banned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                UNIQUE(event_id, user_id)
            )
        """)
        await db.execute(create_query)

        # Create indexes
        await db.execute(text("CREATE INDEX idx_event_bans_event_id ON event_bans(event_id)"))
        await db.execute(text("CREATE INDEX idx_event_bans_user_id ON event_bans(user_id)"))

        await db.commit()
        print("  Table event_bans created successfully")


if __name__ == "__main__":
    asyncio.run(create_event_bans_table())
