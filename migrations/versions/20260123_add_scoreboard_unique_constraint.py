"""Add unique constraint on event_scoreboards (event_id, user_id)

Revision ID: scoreboard_unique_001
Revises: fish_translations_001
Create Date: 2026-01-23

Fixes race condition where concurrent catch validations could create
duplicate scoreboard entries for the same user in the same event.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'scoreboard_unique_001'
down_revision: Union[str, None] = 'fish_translations_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Clean up duplicates and add unique constraint."""
    # Step 1: Delete duplicate scoreboard entries, keeping the one with highest total_catches
    # For ties, keep the one with lowest id (oldest)
    op.execute("""
        DELETE FROM event_scoreboards
        WHERE id NOT IN (
            SELECT DISTINCT ON (event_id, user_id) id
            FROM event_scoreboards
            ORDER BY event_id, user_id, total_catches DESC, id ASC
        )
    """)

    # Step 2: Add unique constraint
    op.create_unique_constraint(
        'uq_event_scoreboard_event_user',
        'event_scoreboards',
        ['event_id', 'user_id']
    )


def downgrade() -> None:
    """Remove unique constraint."""
    op.drop_constraint('uq_event_scoreboard_event_user', 'event_scoreboards', type_='unique')
