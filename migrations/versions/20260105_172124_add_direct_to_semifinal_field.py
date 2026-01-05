"""add_direct_to_semifinal_field

Revision ID: 86b7d1b8cf9c
Revises: 20260101_add_tiebreaker_cols
Create Date: 2026-01-05 17:21:24.748310+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '86b7d1b8cf9c'
down_revision: Union[str, None] = '20260101_add_tiebreaker_cols'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add direct_to_semifinal column - how many bypass requalification and go direct to semifinals
    # Default is 2 (when requalification enabled, top 2 go direct)
    op.add_column(
        'ta_event_settings',
        sa.Column('direct_to_semifinal', sa.Integer(), nullable=False, server_default='2')
    )
    # Remove server_default after adding
    op.alter_column('ta_event_settings', 'direct_to_semifinal', server_default=None)


def downgrade() -> None:
    op.drop_column('ta_event_settings', 'direct_to_semifinal')
