"""Add route_histories table for GPS tracking data.

Stores compressed route history for event participants,
replacing Firebase Firestore storage.
"""

from typing import Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'route_hist_001'
down_revision: Union[str, None] = 'rm_tsf_sector_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'route_histories',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('events.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user_accounts.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('display_name', sa.String(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('ended_at', sa.DateTime(), nullable=False),
        sa.Column('total_distance_km', sa.Float(), default=0.0),
        sa.Column('average_speed_kmh', sa.Float(), default=0.0),
        sa.Column('max_speed_kmh', sa.Float(), default=0.0),
        sa.Column('total_time_minutes', sa.Integer(), default=0),
        sa.Column('geofence_violations', sa.Integer(), default=0),
        sa.Column('time_outside_geofence_minutes', sa.Integer(), default=0),
        sa.Column('point_count', sa.Integer(), default=0),
        sa.Column('points', JSONB(), default=list),
        sa.Column('created_at', sa.DateTime(), default=sa.func.now()),
    )

    # Unique constraint: one route per user per event
    op.create_index(
        'ix_route_histories_event_user',
        'route_histories',
        ['event_id', 'user_id'],
        unique=True
    )


def downgrade() -> None:
    op.drop_index('ix_route_histories_event_user', table_name='route_histories')
    op.drop_table('route_histories')
