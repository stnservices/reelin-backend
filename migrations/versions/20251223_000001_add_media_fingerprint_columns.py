"""add_media_fingerprint_columns

Revision ID: 20251223_000001
Revises: 044d655df871
Create Date: 2025-12-23 00:00:01.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20251223_000001'
down_revision: Union[str, None] = '044d655df871'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add media fingerprint columns to catches table
    op.add_column('catches', sa.Column('sha256_original', sa.String(64), nullable=True))
    op.add_column('catches', sa.Column('original_mime_type', sa.String(50), nullable=True))
    op.add_column('catches', sa.Column('original_size_bytes', sa.Integer(), nullable=True))
    op.add_column('catches', sa.Column('video_duration_seconds', sa.Float(), nullable=True))
    op.add_column('catches', sa.Column('poster_url', sa.String(500), nullable=True))

    # Create index on sha256_original for fast duplicate lookups
    op.create_index('ix_catches_sha256_original', 'catches', ['sha256_original'])

    # Create unique constraint for duplicate prevention per user per event
    op.create_unique_constraint(
        'uq_catches_event_user_sha256',
        'catches',
        ['event_id', 'user_id', 'sha256_original']
    )


def downgrade() -> None:
    # Drop the unique constraint first
    op.drop_constraint('uq_catches_event_user_sha256', 'catches', type_='unique')

    # Drop the index
    op.drop_index('ix_catches_sha256_original', table_name='catches')

    # Drop the columns
    op.drop_column('catches', 'poster_url')
    op.drop_column('catches', 'video_duration_seconds')
    op.drop_column('catches', 'original_size_bytes')
    op.drop_column('catches', 'original_mime_type')
    op.drop_column('catches', 'sha256_original')
