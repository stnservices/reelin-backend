"""add_event_validators_and_admin_logs

Revision ID: a1b2c3d4e5f6
Revises: cb4d93ed7d46
Create Date: 2025-12-15 00:00:01.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'cb4d93ed7d46'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create event_validators junction table
    op.create_table(
        'event_validators',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('validator_id', sa.Integer(), nullable=False),
        sa.Column('assigned_by_id', sa.Integer(), nullable=True),
        sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['validator_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_by_id'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'validator_id', name='uq_event_validator')
    )
    op.create_index(op.f('ix_event_validators_id'), 'event_validators', ['id'], unique=False)
    op.create_index(op.f('ix_event_validators_event_id'), 'event_validators', ['event_id'], unique=False)
    op.create_index(op.f('ix_event_validators_validator_id'), 'event_validators', ['validator_id'], unique=False)

    # Create admin_action_logs table
    op.create_table(
        'admin_action_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('admin_id', sa.Integer(), nullable=True),
        sa.Column('action_type', sa.String(length=50), nullable=False),
        sa.Column('target_user_id', sa.Integer(), nullable=True),
        sa.Column('target_event_id', sa.Integer(), nullable=True),
        sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['admin_id'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['target_user_id'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['target_event_id'], ['events.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_admin_action_logs_id'), 'admin_action_logs', ['id'], unique=False)
    op.create_index(op.f('ix_admin_action_logs_admin_id'), 'admin_action_logs', ['admin_id'], unique=False)
    op.create_index(op.f('ix_admin_action_logs_action_type'), 'admin_action_logs', ['action_type'], unique=False)
    op.create_index(op.f('ix_admin_action_logs_target_user_id'), 'admin_action_logs', ['target_user_id'], unique=False)
    op.create_index(op.f('ix_admin_action_logs_target_event_id'), 'admin_action_logs', ['target_event_id'], unique=False)


def downgrade() -> None:
    # Drop admin_action_logs table
    op.drop_index(op.f('ix_admin_action_logs_target_event_id'), table_name='admin_action_logs')
    op.drop_index(op.f('ix_admin_action_logs_target_user_id'), table_name='admin_action_logs')
    op.drop_index(op.f('ix_admin_action_logs_action_type'), table_name='admin_action_logs')
    op.drop_index(op.f('ix_admin_action_logs_admin_id'), table_name='admin_action_logs')
    op.drop_index(op.f('ix_admin_action_logs_id'), table_name='admin_action_logs')
    op.drop_table('admin_action_logs')

    # Drop event_validators table
    op.drop_index(op.f('ix_event_validators_validator_id'), table_name='event_validators')
    op.drop_index(op.f('ix_event_validators_event_id'), table_name='event_validators')
    op.drop_index(op.f('ix_event_validators_id'), table_name='event_validators')
    op.drop_table('event_validators')
