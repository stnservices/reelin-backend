"""Add ads_enabled column to app_settings.

Global toggle to enable/disable ads for all users from admin panel.
"""

from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = 'ads_enabled_001'
down_revision: Union[str, None] = 'scoring_config_nullable_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ads_enabled column with default True."""
    op.add_column(
        'app_settings',
        sa.Column('ads_enabled', sa.Boolean(), nullable=False, server_default='true')
    )


def downgrade() -> None:
    """Remove ads_enabled column."""
    op.drop_column('app_settings', 'ads_enabled')
