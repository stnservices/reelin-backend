"""Add organizer permission tables.

Revision ID: 20251229_170000
Revises: 20251229_160000
Create Date: 2025-12-29 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20251229_170000"
down_revision: Union[str, None] = "20251229_160000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create organizer_event_type_access table
    op.create_table(
        "organizer_event_type_access",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_type_id", sa.Integer(), nullable=False),
        sa.Column("granted_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_accounts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_type_id"],
            ["event_types.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_id"],
            ["user_accounts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "event_type_id", name="uq_organizer_event_type"),
    )
    op.create_index(
        "ix_organizer_event_type_access_user_id",
        "organizer_event_type_access",
        ["user_id"],
    )
    op.create_index(
        "ix_organizer_event_type_access_event_type_id",
        "organizer_event_type_access",
        ["event_type_id"],
    )

    # Create national_event_organizers table
    op.create_table(
        "national_event_organizers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("granted_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_accounts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_id"],
            ["user_accounts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_national_event_organizers_user_id",
        "national_event_organizers",
        ["user_id"],
        unique=True,
    )

    # Data migration: Grant all existing organizers access to all active event types
    # This ensures backwards compatibility - existing organizers keep their current capabilities
    op.execute(
        """
        INSERT INTO organizer_event_type_access (user_id, event_type_id, granted_at, is_active)
        SELECT DISTINCT up.user_id, et.id, NOW(), true
        FROM user_profiles up
        CROSS JOIN event_types et
        WHERE up.roles::jsonb ? 'organizer'
        AND et.is_active = true
        ON CONFLICT (user_id, event_type_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("national_event_organizers")
    op.drop_table("organizer_event_type_access")
