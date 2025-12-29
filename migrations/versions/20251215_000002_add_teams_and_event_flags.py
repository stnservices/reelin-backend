"""Add teams and event classification flags.

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2025-12-15 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6g7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to events table
    op.add_column('events', sa.Column('is_team_event', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('events', sa.Column('is_national_event', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('events', sa.Column('is_tournament_event', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('events', sa.Column('min_team_size', sa.Integer(), nullable=True))
    op.add_column('events', sa.Column('max_team_size', sa.Integer(), nullable=True))

    # Create teams table
    op.create_table(
        'teams',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('team_number', sa.Integer(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('logo_url', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['user_accounts.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'name', name='uq_team_event_name')
    )
    op.create_index(op.f('ix_teams_id'), 'teams', ['id'], unique=False)
    op.create_index(op.f('ix_teams_event_id'), 'teams', ['event_id'], unique=False)
    op.create_index(op.f('ix_teams_created_by_id'), 'teams', ['created_by_id'], unique=False)

    # Create team_members table
    op.create_table(
        'team_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('enrollment_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False, server_default='member'),
        sa.Column('added_by_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['enrollment_id'], ['event_enrollments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['added_by_id'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('team_id', 'enrollment_id', name='uq_team_member_enrollment')
    )
    op.create_index(op.f('ix_team_members_id'), 'team_members', ['id'], unique=False)
    op.create_index(op.f('ix_team_members_team_id'), 'team_members', ['team_id'], unique=False)
    op.create_index(op.f('ix_team_members_enrollment_id'), 'team_members', ['enrollment_id'], unique=False)


def downgrade() -> None:
    # Drop team_members table
    op.drop_index(op.f('ix_team_members_enrollment_id'), table_name='team_members')
    op.drop_index(op.f('ix_team_members_team_id'), table_name='team_members')
    op.drop_index(op.f('ix_team_members_id'), table_name='team_members')
    op.drop_table('team_members')

    # Drop teams table
    op.drop_index(op.f('ix_teams_created_by_id'), table_name='teams')
    op.drop_index(op.f('ix_teams_event_id'), table_name='teams')
    op.drop_index(op.f('ix_teams_id'), table_name='teams')
    op.drop_table('teams')

    # Remove columns from events table
    op.drop_column('events', 'max_team_size')
    op.drop_column('events', 'min_team_size')
    op.drop_column('events', 'is_tournament_event')
    op.drop_column('events', 'is_national_event')
    op.drop_column('events', 'is_team_event')
