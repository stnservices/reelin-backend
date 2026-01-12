"""Add enhanced moderation detection fields.

Revision ID: add_enhanced_moderation_fields
Revises: add_profile_picture_moderation
Create Date: 2026-01-12

Adds label detection and OCR text fields to profile_picture_moderation table
for detecting offensive gestures and hate speech text.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "add_enhanced_moderation_fields"
down_revision: Union[str, None] = "add_profile_picture_moderation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add enhanced detection fields to profile_picture_moderation
    op.add_column(
        "profile_picture_moderation",
        sa.Column("detected_labels", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "profile_picture_moderation",
        sa.Column("detected_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "profile_picture_moderation",
        sa.Column("offensive_labels_found", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "profile_picture_moderation",
        sa.Column("offensive_text_found", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("profile_picture_moderation", "offensive_text_found")
    op.drop_column("profile_picture_moderation", "offensive_labels_found")
    op.drop_column("profile_picture_moderation", "detected_text")
    op.drop_column("profile_picture_moderation", "detected_labels")
