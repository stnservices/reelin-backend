"""Add team_id and club_id to event_scoreboards for tracking.

This migration adds:
- team_id: Track which team the user belonged to during the event (for team events)
- club_id: Track which club the user was an active member of at event time

This enables:
- Team leaderboard aggregation without complex joins
- Historical club performance tracking
- Club rankings across events

Revision ID: 20251219_000002
Revises: 20251219_000001
Create Date: 2024-12-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251219_000002'
down_revision = '20251219_000001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add team_id column to event_scoreboards
    op.add_column(
        'event_scoreboards',
        sa.Column('team_id', sa.Integer(), nullable=True)
    )

    # Add foreign key constraint for team_id
    op.create_foreign_key(
        'fk_event_scoreboards_team_id',
        'event_scoreboards',
        'teams',
        ['team_id'],
        ['id'],
        ondelete='SET NULL'
    )

    # Add index for faster team-based queries
    op.create_index(
        'ix_event_scoreboards_team_id',
        'event_scoreboards',
        ['team_id']
    )

    # Add club_id column to event_scoreboards
    op.add_column(
        'event_scoreboards',
        sa.Column('club_id', sa.Integer(), nullable=True)
    )

    # Add foreign key constraint for club_id
    op.create_foreign_key(
        'fk_event_scoreboards_club_id',
        'event_scoreboards',
        'clubs',
        ['club_id'],
        ['id'],
        ondelete='SET NULL'
    )

    # Add index for faster club-based queries
    op.create_index(
        'ix_event_scoreboards_club_id',
        'event_scoreboards',
        ['club_id']
    )


def downgrade() -> None:
    # Drop club_id index
    op.drop_index('ix_event_scoreboards_club_id', table_name='event_scoreboards')

    # Drop club_id foreign key
    op.drop_constraint('fk_event_scoreboards_club_id', 'event_scoreboards', type_='foreignkey')

    # Drop club_id column
    op.drop_column('event_scoreboards', 'club_id')

    # Drop team_id index
    op.drop_index('ix_event_scoreboards_team_id', table_name='event_scoreboards')

    # Drop team_id foreign key
    op.drop_constraint('fk_event_scoreboards_team_id', 'event_scoreboards', type_='foreignkey')

    # Drop team_id column
    op.drop_column('event_scoreboards', 'team_id')
