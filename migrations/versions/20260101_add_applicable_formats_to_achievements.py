"""Add applicable_formats to achievement_definitions.

This migration adds:
- applicable_formats: JSONB column for format applicability

Valid values:
- ["sf"] - Street Fishing only
- ["ta"] - Trout Area only
- ["tsf"] - Trout Shore only
- ["sf", "ta"] - SF and TA
- ["ta", "tsf"] - TA and TSF
- ["sf", "ta", "tsf"] - All formats
- null - Applies to all formats (default, backward compatible)

Revision ID: 20260101_add_formats
Revises: 20260101_add_tsf_stats
Create Date: 2026-01-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = '20260101_add_formats'
down_revision = '20260101_add_tsf_stats'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add applicable_formats column (JSONB, nullable)
    op.add_column('achievement_definitions',
        sa.Column('applicable_formats', JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column('achievement_definitions', 'applicable_formats')
