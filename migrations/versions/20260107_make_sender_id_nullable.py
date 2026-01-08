"""Make sender_id nullable for non-auth contact submissions.

Allows non-authenticated visitors to submit contact messages
without a user account. Their info is captured in sender_name,
sender_email, and sender_phone fields.

Revision ID: make_sender_id_nullable
Revises: add_partners_table
Create Date: 2026-01-07
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'make_sender_id_nullable'
down_revision = 'add_partners_table'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make sender_id nullable for non-authenticated contact submissions
    op.alter_column('admin_messages', 'sender_id',
                    existing_type=sa.Integer(),
                    nullable=True)

    # Drop the existing foreign key constraint and recreate without CASCADE on delete
    # This prevents issues when the sender_id is NULL
    op.drop_constraint('admin_messages_sender_id_fkey', 'admin_messages', type_='foreignkey')
    op.create_foreign_key(
        'admin_messages_sender_id_fkey',
        'admin_messages', 'user_accounts',
        ['sender_id'], ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # First set any NULL sender_id to a default admin user (id=1)
    op.execute("UPDATE admin_messages SET sender_id = 1 WHERE sender_id IS NULL")

    # Drop and recreate constraint with CASCADE
    op.drop_constraint('admin_messages_sender_id_fkey', 'admin_messages', type_='foreignkey')
    op.create_foreign_key(
        'admin_messages_sender_id_fkey',
        'admin_messages', 'user_accounts',
        ['sender_id'], ['id'],
        ondelete='CASCADE'
    )

    # Make sender_id NOT NULL again
    op.alter_column('admin_messages', 'sender_id',
                    existing_type=sa.Integer(),
                    nullable=False)
