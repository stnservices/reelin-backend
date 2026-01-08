"""Remove sponsor tier column and migrate to display_order.

Revision ID: remove_sponsor_tier
Revises: make_sender_id_nullable
Create Date: 2026-01-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'remove_sponsor_tier'
down_revision: Union[str, None] = 'make_sender_id_nullable'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Migrate tier values to display_order
    # This preserves the tier-based ordering by assigning ranges:
    # platinum: 100+, gold: 200+, silver: 300+, bronze: 400+, partner: 500+
    op.execute("""
        UPDATE sponsors SET display_order = CASE
            WHEN tier = 'platinum' THEN 100 + COALESCE(display_order, 0)
            WHEN tier = 'gold' THEN 200 + COALESCE(display_order, 0)
            WHEN tier = 'silver' THEN 300 + COALESCE(display_order, 0)
            WHEN tier = 'bronze' THEN 400 + COALESCE(display_order, 0)
            ELSE 500 + COALESCE(display_order, 0)
        END
    """)

    # Step 2: Drop the tier index
    op.drop_index('ix_sponsors_tier', table_name='sponsors')

    # Step 3: Drop the tier column
    op.drop_column('sponsors', 'tier')


def downgrade() -> None:
    # Add tier column back with default 'partner'
    op.add_column('sponsors', sa.Column('tier', sa.String(20), nullable=False, server_default='partner'))

    # Recreate the tier index
    op.create_index('ix_sponsors_tier', 'sponsors', ['tier'])
