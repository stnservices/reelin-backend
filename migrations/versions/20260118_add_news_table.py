"""Add news table for platform news and announcements.

Revision ID: add_news_table
Revises: hall_of_fame_and_top_anglers
Create Date: 2026-01-18

This migration adds the news table for organizers/admins to post
news articles displayed on the public landing page.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_news_table'
down_revision: Union[str, None] = 'hall_of_fame_and_top_anglers'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create news table."""
    op.create_table(
        'news',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('excerpt', sa.String(500), nullable=True),
        sa.Column('featured_image_url', sa.String(500), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('is_published', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['user_accounts.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Create indexes for common queries
    op.create_index('ix_news_is_published', 'news', ['is_published'])
    op.create_index('ix_news_is_deleted', 'news', ['is_deleted'])
    op.create_index('ix_news_published_at', 'news', ['published_at'])
    op.create_index('ix_news_created_by_id', 'news', ['created_by_id'])

    # Composite index for public queries (published + not deleted + order by published_at)
    op.create_index(
        'ix_news_public_list',
        'news',
        ['is_published', 'is_deleted', 'published_at'],
    )


def downgrade() -> None:
    """Drop news table."""
    op.drop_index('ix_news_public_list', table_name='news')
    op.drop_index('ix_news_created_by_id', table_name='news')
    op.drop_index('ix_news_published_at', table_name='news')
    op.drop_index('ix_news_is_deleted', table_name='news')
    op.drop_index('ix_news_is_published', table_name='news')
    op.drop_table('news')
