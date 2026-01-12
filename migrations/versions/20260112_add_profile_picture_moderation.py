"""Add profile picture moderation.

Revision ID: add_profile_picture_moderation
Revises: 20260111_add_sf_draw_number_trigger
Create Date: 2026-01-12

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "add_profile_picture_moderation"
down_revision: Union[str, None] = "sf_draw_number_trigger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add profile_picture_status column to user_profiles
    op.add_column(
        "user_profiles",
        sa.Column(
            "profile_picture_status",
            sa.String(20),
            nullable=False,
            server_default="approved",
        ),
    )

    # Create profile_picture_moderation table for audit logging
    op.create_table(
        "profile_picture_moderation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        # SafeSearch scores (0-5 likelihood levels from Google Vision)
        sa.Column("adult_score", sa.Integer(), nullable=True),
        sa.Column("violence_score", sa.Integer(), nullable=True),
        sa.Column("racy_score", sa.Integer(), nullable=True),
        # Rejection info
        sa.Column("rejection_reason", sa.String(50), nullable=True),
        # Processing info
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("raw_response", postgresql.JSONB(), nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_accounts.id"],
            ondelete="CASCADE",
        ),
    )

    # Create indexes
    op.create_index(
        "ix_profile_picture_moderation_user_id",
        "profile_picture_moderation",
        ["user_id"],
    )
    op.create_index(
        "ix_profile_picture_moderation_status",
        "profile_picture_moderation",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_profile_picture_moderation_status")
    op.drop_index("ix_profile_picture_moderation_user_id")
    op.drop_table("profile_picture_moderation")
    op.drop_column("user_profiles", "profile_picture_status")
