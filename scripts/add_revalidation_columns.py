"""
Migration script to add revalidation columns to catches table.
Allows validators to change validation status after initial validation.
"""

import asyncio

from sqlalchemy import text

from app.database import engine


async def migrate():
    """Add revalidation columns to catches table."""
    print("Adding revalidation columns to catches table...")

    async with engine.begin() as conn:
        # Check if columns already exist
        check_query = text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'catches'
            AND column_name = 'revalidated_by_id'
        """)
        result = await conn.execute(check_query)
        if result.fetchone():
            print("Revalidation columns already exist, skipping...")
            return

        # Add revalidated_by_id column
        await conn.execute(text("""
            ALTER TABLE catches
            ADD COLUMN revalidated_by_id INTEGER REFERENCES user_accounts(id) ON DELETE SET NULL
        """))
        print("  Added revalidated_by_id column")

        # Add revalidated_at column
        await conn.execute(text("""
            ALTER TABLE catches
            ADD COLUMN revalidated_at TIMESTAMP WITH TIME ZONE
        """))
        print("  Added revalidated_at column")

        # Add revalidation_reason column
        await conn.execute(text("""
            ALTER TABLE catches
            ADD COLUMN revalidation_reason TEXT
        """))
        print("  Added revalidation_reason column")

        # Create index for revalidated_by_id
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_catches_revalidated_by_id
            ON catches(revalidated_by_id)
        """))
        print("  Created index on revalidated_by_id")

    print("\nRevalidation columns added successfully!")


if __name__ == "__main__":
    asyncio.run(migrate())
