"""Add recommendation_dismissals table

Revision ID: 20251228_200001
Revises: 20251228_100259
Create Date: 2024-12-28 20:00:01.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251228_200001'
down_revision = 'wp72a1b2c3d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create recommendation_dismissals table
    op.create_table(
        'recommendation_dismissals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('item_type', sa.String(20), nullable=False),  # 'event' or 'angler'
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('dismissed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'item_type', 'item_id', name='uq_dismissal_user_type_item')
    )

    # Create index for faster lookups
    op.create_index('idx_dismissals_user', 'recommendation_dismissals', ['user_id'])
    op.create_index('idx_dismissals_user_type', 'recommendation_dismissals', ['user_id', 'item_type'])


def downgrade() -> None:
    op.drop_index('idx_dismissals_user_type', table_name='recommendation_dismissals')
    op.drop_index('idx_dismissals_user', table_name='recommendation_dismissals')
    op.drop_table('recommendation_dismissals')
