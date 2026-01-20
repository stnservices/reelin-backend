"""Add is_test column to events table.

Revision ID: add_is_test_to_events
Revises: add_news_table
Create Date: 2026-01-20

This migration adds the is_test column to events table.
Test events are excluded from stats, achievements, rankings, etc.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_is_test_to_events'
down_revision: Union[str, None] = 'add_news_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add is_test column to events table."""
    op.add_column(
        'events',
        sa.Column('is_test', sa.Boolean(), nullable=False, server_default='false')
    )
    op.create_index('ix_events_is_test', 'events', ['is_test'])


def downgrade() -> None:
    """Remove is_test column from events table."""
    op.drop_index('ix_events_is_test', table_name='events')
    op.drop_column('events', 'is_test')
