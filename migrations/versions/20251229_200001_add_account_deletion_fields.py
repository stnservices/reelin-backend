"""Add account deletion fields and settings.

Revision ID: 20251229_200001
Revises: c4ed2752cf78
Create Date: 2025-12-29

Adds:
- deletion_scheduled_at field to user_accounts for grace period tracking
- account_deletion_grace_period_days setting to pro_settings
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251229_200001"
down_revision = "c4ed2752cf78"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add deletion_scheduled_at column to user_accounts
    op.add_column(
        "user_accounts",
        sa.Column("deletion_scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_user_accounts_deletion_scheduled_at",
        "user_accounts",
        ["deletion_scheduled_at"],
        unique=False,
    )

    # Add grace period setting to pro_settings
    op.execute(
        """
        INSERT INTO pro_settings (key, value, description, updated_at)
        VALUES (
            'account_deletion_grace_period_days',
            '30',
            'Number of days users have to recover their account after requesting deletion (0 = immediate deletion)',
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    # Remove the setting
    op.execute(
        """
        DELETE FROM pro_settings
        WHERE key = 'account_deletion_grace_period_days'
        """
    )

    # Remove the index and column
    op.drop_index("ix_user_accounts_deletion_scheduled_at", table_name="user_accounts")
    op.drop_column("user_accounts", "deletion_scheduled_at")
