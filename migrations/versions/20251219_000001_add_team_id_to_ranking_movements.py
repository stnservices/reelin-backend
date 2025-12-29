"""Add team_id to ranking_movements for team event support.

Revision ID: 20251219_000001
Revises: 20251217_000001_add_sponsor_ownership
Create Date: 2024-12-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251219_000001'
down_revision = 'c5e65296fa53'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add team_id column to ranking_movements
    op.add_column(
        'ranking_movements',
        sa.Column('team_id', sa.Integer(), nullable=True)
    )

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_ranking_movements_team_id',
        'ranking_movements',
        'teams',
        ['team_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # Add index for faster team-based queries
    op.create_index(
        'ix_ranking_movements_team_id',
        'ranking_movements',
        ['team_id']
    )

    # Make user_id nullable (for team events, we track team_id instead)
    op.alter_column(
        'ranking_movements',
        'user_id',
        existing_type=sa.Integer(),
        nullable=True
    )


def downgrade() -> None:
    # Make user_id required again
    op.alter_column(
        'ranking_movements',
        'user_id',
        existing_type=sa.Integer(),
        nullable=False
    )

    # Drop index
    op.drop_index('ix_ranking_movements_team_id', table_name='ranking_movements')

    # Drop foreign key
    op.drop_constraint('fk_ranking_movements_team_id', 'ranking_movements', type_='foreignkey')

    # Drop column
    op.drop_column('ranking_movements', 'team_id')
