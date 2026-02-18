"""Remove ads_enabled column from app_settings.

AdMob ads removed entirely from the platform.
"""

from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = 'remove_ads_001'
down_revision: Union[str, None] = 'audit_tracking_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop ads_enabled column."""
    op.drop_column('app_settings', 'ads_enabled')


def downgrade() -> None:
    """Re-add ads_enabled column."""
    op.add_column(
        'app_settings',
        sa.Column('ads_enabled', sa.Boolean(), nullable=False, server_default='true')
    )
