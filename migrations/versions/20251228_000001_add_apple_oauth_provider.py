"""Add Apple to OAuth provider enum.

Revision ID: f8a9b7c6d5e4
Revises: 9a3b5c7d8e1f
Create Date: 2025-12-28

Story 8.8: Add Apple Sign In support for iOS.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f8a9b7c6d5e4'
down_revision: Union[str, None] = '9a3b5c7d8e1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add 'APPLE' value to oauth_provider enum (uppercase to match GOOGLE, FACEBOOK)
    # PostgreSQL allows adding values to enums without recreating
    op.execute("ALTER TYPE oauth_provider ADD VALUE IF NOT EXISTS 'APPLE'")


def downgrade() -> None:
    # Note: PostgreSQL does not support removing enum values directly
    # To downgrade, we would need to:
    # 1. Create a new enum without 'apple'
    # 2. Update the column to use the new enum
    # 3. Drop the old enum
    # For now, we leave 'apple' in place as it's harmless
    pass
