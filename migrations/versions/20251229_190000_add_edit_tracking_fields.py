"""Add edit tracking fields to TSFLegPosition and TAMatch.

Enables validators and organizers to correct mistakes with audit trail.

Revision ID: 20251229_190000
Revises: 20251229_180000
Create Date: 2025-12-29
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20251229_190000"
down_revision: Union[str, None] = "20251229_180000"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Add edit tracking fields to tsf_leg_positions
    op.add_column('tsf_leg_positions', sa.Column('edited_by_id', sa.Integer(), nullable=True))
    op.add_column('tsf_leg_positions', sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tsf_leg_positions', sa.Column('previous_fish_count', sa.Integer(), nullable=True))
    op.add_column('tsf_leg_positions', sa.Column('previous_position_value', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_tsf_leg_positions_edited_by',
        'tsf_leg_positions', 'user_accounts',
        ['edited_by_id'], ['id'],
        ondelete='SET NULL'
    )

    # Add edit tracking fields to ta_matches
    op.add_column('ta_matches', sa.Column('edited_by_id', sa.Integer(), nullable=True))
    op.add_column('ta_matches', sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('ta_matches', sa.Column('previous_a_catches', sa.Integer(), nullable=True))
    op.add_column('ta_matches', sa.Column('previous_b_catches', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_ta_matches_edited_by',
        'ta_matches', 'user_accounts',
        ['edited_by_id'], ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # Remove from ta_matches
    op.drop_constraint('fk_ta_matches_edited_by', 'ta_matches', type_='foreignkey')
    op.drop_column('ta_matches', 'previous_b_catches')
    op.drop_column('ta_matches', 'previous_a_catches')
    op.drop_column('ta_matches', 'edited_at')
    op.drop_column('ta_matches', 'edited_by_id')

    # Remove from tsf_leg_positions
    op.drop_constraint('fk_tsf_leg_positions_edited_by', 'tsf_leg_positions', type_='foreignkey')
    op.drop_column('tsf_leg_positions', 'previous_position_value')
    op.drop_column('tsf_leg_positions', 'previous_fish_count')
    op.drop_column('tsf_leg_positions', 'edited_at')
    op.drop_column('tsf_leg_positions', 'edited_by_id')
