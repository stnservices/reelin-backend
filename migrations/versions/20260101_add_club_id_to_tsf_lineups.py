"""Add club_id to tsf_lineups for club-based reporting.

This migration adds:
- club_id: Track which club the user was an active member of at enrollment time

This enables:
- Club-based TSF performance tracking
- Club rankings across TSF events

Revision ID: 20260101_add_club_id_tsf
Revises: 20260101_add_club_id
Create Date: 2026-01-01

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260101_add_club_id_tsf'
down_revision = '20260101_add_club_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add club_id column to tsf_lineups
    op.add_column(
        'tsf_lineups',
        sa.Column('club_id', sa.Integer(), nullable=True)
    )

    # Add foreign key constraint for club_id
    op.create_foreign_key(
        'fk_tsf_lineups_club_id',
        'tsf_lineups',
        'clubs',
        ['club_id'],
        ['id'],
        ondelete='SET NULL'
    )

    # Add index for faster club-based queries
    op.create_index(
        'ix_tsf_lineups_club_id',
        'tsf_lineups',
        ['club_id']
    )


def downgrade() -> None:
    # Drop club_id index
    op.drop_index('ix_tsf_lineups_club_id', table_name='tsf_lineups')

    # Drop club_id foreign key
    op.drop_constraint('fk_tsf_lineups_club_id', 'tsf_lineups', type_='foreignkey')

    # Drop club_id column
    op.drop_column('tsf_lineups', 'club_id')
