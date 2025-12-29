"""add_user_follows_table

Revision ID: 9a3b5c7d8e1f
Revises: 78e7863d7779
Create Date: 2025-12-27 00:00:02.000000+00:00

Epic 8 Story 8.2: Add user_follows table for follow system.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a3b5c7d8e1f'
down_revision: Union[str, None] = '78e7863d7779'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create user_follows table
    op.create_table(
        'user_follows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('follower_id', sa.Integer(), nullable=False),
        sa.Column('following_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['follower_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['following_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('follower_id', 'following_id', name='uq_user_follows_follower_following'),
        sa.CheckConstraint('follower_id != following_id', name='ck_user_follows_no_self_follow'),
    )

    # Create indexes for efficient lookups
    op.create_index('ix_user_follows_id', 'user_follows', ['id'], unique=False)
    op.create_index('ix_user_follows_follower_id', 'user_follows', ['follower_id'], unique=False)
    op.create_index('ix_user_follows_following_id', 'user_follows', ['following_id'], unique=False)


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_user_follows_following_id', table_name='user_follows')
    op.drop_index('ix_user_follows_follower_id', table_name='user_follows')
    op.drop_index('ix_user_follows_id', table_name='user_follows')

    # Drop table
    op.drop_table('user_follows')
