"""Add organizer_messages table for contact form.

Revision ID: 8a3c5e7d9f12
Revises: 0f8de3b121c4
Create Date: 2024-12-24 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a3c5e7d9f12'
down_revision: Union[str, None] = '0f8de3b121c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'organizer_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('sender_id', sa.Integer(), nullable=False),
        sa.Column('subject', sa.String(200), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('sender_name', sa.String(200), nullable=False),
        sa.Column('sender_email', sa.String(255), nullable=False),
        sa.Column('sender_phone', sa.String(50), nullable=True),
        sa.Column('is_enrolled', sa.Boolean(), nullable=False, default=False),
        sa.Column('is_read', sa.Boolean(), nullable=False, default=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sender_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for common queries
    op.create_index('ix_organizer_messages_id', 'organizer_messages', ['id'])
    op.create_index('ix_organizer_messages_event_id', 'organizer_messages', ['event_id'])
    op.create_index('ix_organizer_messages_sender_id', 'organizer_messages', ['sender_id'])
    op.create_index('ix_organizer_messages_is_read', 'organizer_messages', ['is_read'])
    op.create_index('ix_organizer_messages_created_at', 'organizer_messages', ['created_at'])

    # Composite index for rate limiting queries (event_id, sender_id, created_at)
    op.create_index(
        'ix_organizer_messages_rate_limit',
        'organizer_messages',
        ['event_id', 'sender_id', 'created_at']
    )


def downgrade() -> None:
    op.drop_index('ix_organizer_messages_rate_limit', 'organizer_messages')
    op.drop_index('ix_organizer_messages_created_at', 'organizer_messages')
    op.drop_index('ix_organizer_messages_is_read', 'organizer_messages')
    op.drop_index('ix_organizer_messages_sender_id', 'organizer_messages')
    op.drop_index('ix_organizer_messages_event_id', 'organizer_messages')
    op.drop_index('ix_organizer_messages_id', 'organizer_messages')
    op.drop_table('organizer_messages')
