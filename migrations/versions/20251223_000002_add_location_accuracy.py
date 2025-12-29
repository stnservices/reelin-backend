"""add_location_accuracy

Revision ID: 20251223_000002
Revises: 20251223_000001
Create Date: 2025-12-23 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20251223_000002'
down_revision: Union[str, None] = '20251223_000001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add location_accuracy column to catches table
    op.add_column('catches', sa.Column('location_accuracy', sa.Float(), nullable=True))


def downgrade() -> None:
    # Drop the column
    op.drop_column('catches', 'location_accuracy')
