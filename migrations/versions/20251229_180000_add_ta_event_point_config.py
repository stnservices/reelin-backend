"""Add TA and TSF Event Point Config tables.

Per-event configurable point values for TA (Trout Area) and TSF (Trout Shore Fishing) competitions.
Allows organizers to customize V/T/T0/L/L0 point values.

Revision ID: 20251229_180000
Revises: 20251229_170000
Create Date: 2025-12-29
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20251229_180000"
down_revision: Union[str, None] = "20251229_170000"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Create ta_event_point_configs table
    op.create_table(
        'ta_event_point_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('victory_points', sa.Numeric(precision=4, scale=2), nullable=False, server_default='3.0'),
        sa.Column('tie_points', sa.Numeric(precision=4, scale=2), nullable=False, server_default='1.5'),
        sa.Column('tie_zero_points', sa.Numeric(precision=4, scale=2), nullable=False, server_default='1.0'),
        sa.Column('loss_points', sa.Numeric(precision=4, scale=2), nullable=False, server_default='0.5'),
        sa.Column('loss_zero_points', sa.Numeric(precision=4, scale=2), nullable=False, server_default='0.0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ta_event_point_configs_id', 'ta_event_point_configs', ['id'], unique=False)
    op.create_index('ix_ta_event_point_configs_event_id', 'ta_event_point_configs', ['event_id'], unique=True)

    # Create tsf_event_point_configs table
    op.create_table(
        'tsf_event_point_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('victory_points', sa.Numeric(precision=4, scale=2), nullable=False, server_default='3.0'),
        sa.Column('tie_points', sa.Numeric(precision=4, scale=2), nullable=False, server_default='1.5'),
        sa.Column('tie_zero_points', sa.Numeric(precision=4, scale=2), nullable=False, server_default='1.0'),
        sa.Column('loss_points', sa.Numeric(precision=4, scale=2), nullable=False, server_default='0.5'),
        sa.Column('loss_zero_points', sa.Numeric(precision=4, scale=2), nullable=False, server_default='0.0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tsf_event_point_configs_id', 'tsf_event_point_configs', ['id'], unique=False)
    op.create_index('ix_tsf_event_point_configs_event_id', 'tsf_event_point_configs', ['event_id'], unique=True)


def downgrade() -> None:
    # Drop TSF table
    op.drop_index('ix_tsf_event_point_configs_event_id', table_name='tsf_event_point_configs')
    op.drop_index('ix_tsf_event_point_configs_id', table_name='tsf_event_point_configs')
    op.drop_table('tsf_event_point_configs')

    # Drop TA table
    op.drop_index('ix_ta_event_point_configs_event_id', table_name='ta_event_point_configs')
    op.drop_index('ix_ta_event_point_configs_id', table_name='ta_event_point_configs')
    op.drop_table('ta_event_point_configs')
