"""Fix APPLE oauth_provider enum case mismatch.

Revision ID: 20251228_210001
Revises: 20251228_200001
Create Date: 2025-12-28 21:00:01.000000

The original migration added 'APPLE' (uppercase) but the Python enum
expects 'apple' (lowercase) to match 'google' and 'facebook'.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20251228_210001'
down_revision = '20251228_200001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The SQLAlchemy ENUM uses Python enum member NAMES (GOOGLE, FACEBOOK, APPLE), not values.
    # The original migration added 'APPLE' which was correct, but the Python code may have
    # changed. This migration ensures 'APPLE' exists in the PostgreSQL enum.
    # Note: The original migration already adds 'APPLE', so this is a no-op.
    op.execute("ALTER TYPE oauth_provider ADD VALUE IF NOT EXISTS 'APPLE'")

    # Also add lowercase variants in case any data was inserted with lowercase values
    op.execute("ALTER TYPE oauth_provider ADD VALUE IF NOT EXISTS 'apple'")
    op.execute("ALTER TYPE oauth_provider ADD VALUE IF NOT EXISTS 'google'")
    op.execute("ALTER TYPE oauth_provider ADD VALUE IF NOT EXISTS 'facebook'")

    # Normalize any lowercase values to uppercase (SQLAlchemy uses enum names = uppercase)
    op.execute("UPDATE social_accounts SET provider = 'APPLE' WHERE provider = 'apple'")
    op.execute("UPDATE social_accounts SET provider = 'GOOGLE' WHERE provider = 'google'")
    op.execute("UPDATE social_accounts SET provider = 'FACEBOOK' WHERE provider = 'facebook'")


def downgrade() -> None:
    # Note: PostgreSQL does not support removing enum values
    # We can convert back to APPLE if needed
    op.execute("UPDATE social_accounts SET provider = 'APPLE' WHERE provider = 'apple'")
