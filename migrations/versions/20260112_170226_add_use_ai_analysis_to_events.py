"""add use_ai_analysis to events

Revision ID: 401ccf68f1a0
Revises: add_ml_auto_validation
Create Date: 2026-01-12 17:02:26.905368+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '401ccf68f1a0'
down_revision: Union[str, None] = 'add_ml_auto_validation'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add use_ai_analysis column with default False
    op.add_column('events', sa.Column('use_ai_analysis', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    op.drop_column('events', 'use_ai_analysis')
