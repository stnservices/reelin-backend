"""Add last_like_notification_at to catches for rate limiting.

Tracks when the last like notification was sent for a catch
to prevent notification spam from rapid like/unlike toggling.
"""

from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = 'like_notif_001'
down_revision: Union[str, None] = 'catch_react_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'catches',
        sa.Column('last_like_notification_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('catches', 'last_like_notification_at')
