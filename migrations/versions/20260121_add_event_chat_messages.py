"""Add event_chat_messages table

Revision ID: chat_messages_001
Revises:
Create Date: 2026-01-21

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'chat_messages_001'
down_revision = '8de901c15bfd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create event_chat_messages table
    op.create_table('event_chat_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('message_type', sa.String(20), nullable=False, server_default='message'),
        sa.Column('is_pinned', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('pinned_by_id', sa.Integer(), nullable=True),
        sa.Column('pinned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('deleted_by_id', sa.Integer(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['pinned_by_id'], ['user_accounts.id'], ),
        sa.ForeignKeyConstraint(['deleted_by_id'], ['user_accounts.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes
    op.create_index('ix_event_chat_messages_id', 'event_chat_messages', ['id'], unique=False)
    op.create_index('ix_event_chat_messages_event_id', 'event_chat_messages', ['event_id'], unique=False)
    op.create_index('ix_event_chat_messages_user_id', 'event_chat_messages', ['user_id'], unique=False)
    op.create_index('ix_event_chat_messages_created_at', 'event_chat_messages', ['created_at'], unique=False)
    op.create_index('ix_event_chat_messages_is_pinned', 'event_chat_messages', ['is_pinned'], unique=False)
    op.create_index('ix_event_chat_messages_is_deleted', 'event_chat_messages', ['is_deleted'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_event_chat_messages_is_deleted', table_name='event_chat_messages')
    op.drop_index('ix_event_chat_messages_is_pinned', table_name='event_chat_messages')
    op.drop_index('ix_event_chat_messages_created_at', table_name='event_chat_messages')
    op.drop_index('ix_event_chat_messages_user_id', table_name='event_chat_messages')
    op.drop_index('ix_event_chat_messages_event_id', table_name='event_chat_messages')
    op.drop_index('ix_event_chat_messages_id', table_name='event_chat_messages')
    op.drop_table('event_chat_messages')
