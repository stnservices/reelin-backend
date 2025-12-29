"""add_social_links_and_privacy_to_user_profile

Revision ID: 78e7863d7779
Revises: 51425b6b7b09
Create Date: 2025-12-27 00:00:01.000000+00:00

Epic 8 Story 8.1: Add social link fields and privacy toggle to user_profiles table.
- facebook_url, instagram_url, tiktok_url, youtube_url (PRO feature)
- is_profile_public (privacy toggle, default true)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '78e7863d7779'
down_revision: Union[str, None] = '51425b6b7b09'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add social link fields (PRO feature)
    op.add_column('user_profiles', sa.Column('facebook_url', sa.String(length=500), nullable=True))
    op.add_column('user_profiles', sa.Column('instagram_url', sa.String(length=500), nullable=True))
    op.add_column('user_profiles', sa.Column('tiktok_url', sa.String(length=500), nullable=True))
    op.add_column('user_profiles', sa.Column('youtube_url', sa.String(length=500), nullable=True))

    # Add privacy toggle (default true = public profile)
    op.add_column('user_profiles', sa.Column('is_profile_public', sa.Boolean(), nullable=False, server_default='true'))


def downgrade() -> None:
    # Remove privacy toggle
    op.drop_column('user_profiles', 'is_profile_public')

    # Remove social link fields
    op.drop_column('user_profiles', 'youtube_url')
    op.drop_column('user_profiles', 'tiktok_url')
    op.drop_column('user_profiles', 'instagram_url')
    op.drop_column('user_profiles', 'facebook_url')
