"""Make scoring_config_id optional for TA events.

TA events use their own point system (TAEventPointConfig) and don't need
scoring_config. This allows scoring_config_id to be NULL for TA events
while still requiring it for SF events (enforced at API level).
"""

from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = 'scoring_config_nullable_001'
down_revision: Union[str, None] = 'like_notif_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Make scoring_config_id nullable in events table."""
    op.alter_column(
        'events',
        'scoring_config_id',
        existing_type=sa.Integer(),
        nullable=True
    )


def downgrade() -> None:
    """Make scoring_config_id non-nullable in events table.

    Note: This will fail if there are any NULL values in the column.
    """
    op.alter_column(
        'events',
        'scoring_config_id',
        existing_type=sa.Integer(),
        nullable=False
    )
