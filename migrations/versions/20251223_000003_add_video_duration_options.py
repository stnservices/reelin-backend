"""add_video_duration_options

Revision ID: 20251223_000003
Revises: 20251223_000002
Create Date: 2025-12-23 18:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20251223_000003'
down_revision: Union[str, None] = '20251223_000002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create video_duration_options table
    op.create_table(
        'video_duration_options',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('seconds', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=50), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('seconds')
    )
    op.create_index(op.f('ix_video_duration_options_id'), 'video_duration_options', ['id'], unique=False)

    # Seed default values
    op.execute("""
        INSERT INTO video_duration_options (seconds, label, display_order) VALUES
        (3, '3 seconds', 1),
        (4, '4 seconds', 2),
        (5, '5 seconds', 3)
    """)


def downgrade() -> None:
    op.drop_index(op.f('ix_video_duration_options_id'), table_name='video_duration_options')
    op.drop_table('video_duration_options')
