"""Add social accounts table and update user_accounts for OAuth.

Revision ID: 20251216_000001
Revises: b2c3d4e5f6g7
Create Date: 2025-12-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20251216_000001'
down_revision: Union[str, None] = 'b2c3d4e5f6g7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create OAuth provider enum type
    oauth_provider_enum = postgresql.ENUM('google', 'facebook', name='oauth_provider', create_type=False)
    oauth_provider_enum.create(op.get_bind(), checkfirst=True)

    # Add avatar_url to user_accounts
    op.add_column('user_accounts', sa.Column('avatar_url', sa.String(500), nullable=True))

    # Make password_hash nullable for social-only accounts
    op.alter_column('user_accounts', 'password_hash',
                    existing_type=sa.String(255),
                    nullable=True)

    # Create social_accounts table
    op.create_table(
        'social_accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('provider', oauth_provider_enum, nullable=False),
        sa.Column('provider_account_id', sa.String(255), nullable=False),
        sa.Column('access_token', sa.String(2048), nullable=True),
        sa.Column('refresh_token', sa.String(2048), nullable=True),
        sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'provider_account_id', name='uq_social_provider_account')
    )
    op.create_index(op.f('ix_social_accounts_id'), 'social_accounts', ['id'], unique=False)
    op.create_index(op.f('ix_social_accounts_user_id'), 'social_accounts', ['user_id'], unique=False)


def downgrade() -> None:
    # Drop social_accounts table
    op.drop_index(op.f('ix_social_accounts_user_id'), table_name='social_accounts')
    op.drop_index(op.f('ix_social_accounts_id'), table_name='social_accounts')
    op.drop_table('social_accounts')

    # Remove avatar_url from user_accounts
    op.drop_column('user_accounts', 'avatar_url')

    # Make password_hash non-nullable again
    # Note: This will fail if there are social-only accounts!
    op.alter_column('user_accounts', 'password_hash',
                    existing_type=sa.String(255),
                    nullable=False)

    # Drop OAuth provider enum type
    oauth_provider_enum = postgresql.ENUM('google', 'facebook', name='oauth_provider')
    oauth_provider_enum.drop(op.get_bind(), checkfirst=True)
