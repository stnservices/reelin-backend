"""Add tiebreaker fields to event_scoreboards for complete ranking data.

This migration adds:
- species_count: Number of distinct fish species caught
- average_length: Average length of all catches (for tiebreaker)
- first_catch_time: Timestamp of first approved catch (earliest wins in tiebreaker)

This enables:
- Complete 6-level tiebreaker support in the scoreboard table
- Export-ready scoreboard data without complex recalculation
- Historical ranking accuracy for post-event reports

Revision ID: 20251219_000003
Revises: 20251219_000002
Create Date: 2024-12-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251219_000003'
down_revision = '20251219_000002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add species_count column
    op.add_column(
        'event_scoreboards',
        sa.Column('species_count', sa.Integer(), nullable=False, server_default='0')
    )

    # Add average_length column
    op.add_column(
        'event_scoreboards',
        sa.Column('average_length', sa.Float(), nullable=False, server_default='0.0')
    )

    # Add first_catch_time column (timestamp of earliest approved catch)
    op.add_column(
        'event_scoreboards',
        sa.Column('first_catch_time', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    # Drop first_catch_time column
    op.drop_column('event_scoreboards', 'first_catch_time')

    # Drop average_length column
    op.drop_column('event_scoreboards', 'average_length')

    # Drop species_count column
    op.drop_column('event_scoreboards', 'species_count')
