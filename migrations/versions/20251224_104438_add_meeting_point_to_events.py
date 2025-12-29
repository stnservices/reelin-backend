"""add_meeting_point_to_events

Revision ID: 2906b68d73fe
Revises: 86b8cb4df4cb
Create Date: 2025-12-24 10:44:38.008008+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2906b68d73fe'
down_revision: Union[str, None] = '86b8cb4df4cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add meeting point fields to events table
    op.add_column('events', sa.Column('meeting_point_lat', sa.Float(), nullable=True))
    op.add_column('events', sa.Column('meeting_point_lng', sa.Float(), nullable=True))
    op.add_column('events', sa.Column('meeting_point_address', sa.String(500), nullable=True))


def downgrade() -> None:
    # Remove meeting point fields from events table
    op.drop_column('events', 'meeting_point_address')
    op.drop_column('events', 'meeting_point_lng')
    op.drop_column('events', 'meeting_point_lat')
