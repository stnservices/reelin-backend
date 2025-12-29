"""Add admin_messages table for platform contact form.

Revision ID: 9b4d6f8e0a23
Revises: 8a3c5e7d9f12
Create Date: 2025-12-25 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b4d6f8e0a23'
down_revision: Union[str, None] = '8a3c5e7d9f12'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'admin_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sender_id', sa.Integer(), nullable=False),
        sa.Column('subject', sa.String(200), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('sender_name', sa.String(200), nullable=False),
        sa.Column('sender_email', sa.String(255), nullable=False),
        sa.Column('sender_phone', sa.String(50), nullable=True),
        sa.Column('is_read', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('read_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['sender_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['read_by_id'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_admin_messages_id', 'admin_messages', ['id'])
    op.create_index('ix_admin_messages_sender_id', 'admin_messages', ['sender_id'])
    op.create_index('ix_admin_messages_is_read', 'admin_messages', ['is_read'])
    op.create_index('ix_admin_messages_created_at', 'admin_messages', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_admin_messages_created_at', table_name='admin_messages')
    op.drop_index('ix_admin_messages_is_read', table_name='admin_messages')
    op.drop_index('ix_admin_messages_sender_id', table_name='admin_messages')
    op.drop_index('ix_admin_messages_id', table_name='admin_messages')
    op.drop_table('admin_messages')
