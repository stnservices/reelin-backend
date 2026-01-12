"""Add ML auto-validation system.

Revision ID: add_ml_auto_validation
Revises: add_enhanced_moderation_fields
Create Date: 2026-01-12

Adds:
- Event ML settings (use_ml_auto_validation, ml_confidence_threshold)
- CatchAiAnalysis validation fields
- AI Moderator system account (Fane AI)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "add_ml_auto_validation"
down_revision: Union[str, None] = "add_enhanced_moderation_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# AI Moderator account details
AI_MODERATOR_EMAIL = "ai_moderator@reelin.ro"
AI_MODERATOR_FIRST_NAME = "Fane"
AI_MODERATOR_LAST_NAME = "AI"


def upgrade() -> None:
    # 1. Add ML auto-validation settings to events table
    op.add_column(
        "events",
        sa.Column("use_ml_auto_validation", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "events",
        sa.Column("ml_confidence_threshold", sa.Float(), nullable=False, server_default="0.85"),
    )

    # 2. Add validation fields to catch_ai_analysis table
    op.add_column(
        "catch_ai_analysis",
        sa.Column("validation_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "catch_ai_analysis",
        sa.Column("validation_recommendation", sa.String(20), nullable=True),
    )
    op.add_column(
        "catch_ai_analysis",
        sa.Column("ai_insights", sa.Text(), nullable=True),
    )
    op.add_column(
        "catch_ai_analysis",
        sa.Column("auto_validated", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "catch_ai_analysis",
        sa.Column("auto_validated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 3. Create AI Moderator system account
    # Use raw SQL to insert the account
    op.execute(f"""
        INSERT INTO user_accounts (email, is_active, is_staff, is_superuser, is_verified, created_at, updated_at)
        SELECT '{AI_MODERATOR_EMAIL}', true, true, false, true, NOW(), NOW()
        WHERE NOT EXISTS (
            SELECT 1 FROM user_accounts WHERE email = '{AI_MODERATOR_EMAIL}'
        );
    """)

    # Create profile for AI Moderator
    op.execute(f"""
        INSERT INTO user_profiles (user_id, first_name, last_name, roles, is_deleted, is_profile_public, profile_picture_status, created_at, updated_at)
        SELECT id, '{AI_MODERATOR_FIRST_NAME}', '{AI_MODERATOR_LAST_NAME}', '["system"]'::jsonb, false, true, 'approved', NOW(), NOW()
        FROM user_accounts
        WHERE email = '{AI_MODERATOR_EMAIL}'
        AND NOT EXISTS (
            SELECT 1 FROM user_profiles WHERE user_id = (
                SELECT id FROM user_accounts WHERE email = '{AI_MODERATOR_EMAIL}'
            )
        );
    """)


def downgrade() -> None:
    # Remove validation fields from catch_ai_analysis
    op.drop_column("catch_ai_analysis", "auto_validated_at")
    op.drop_column("catch_ai_analysis", "auto_validated")
    op.drop_column("catch_ai_analysis", "ai_insights")
    op.drop_column("catch_ai_analysis", "validation_recommendation")
    op.drop_column("catch_ai_analysis", "validation_confidence")

    # Remove ML settings from events
    op.drop_column("events", "ml_confidence_threshold")
    op.drop_column("events", "use_ml_auto_validation")

    # Note: We don't delete the AI Moderator account in downgrade
    # to preserve any validation history that references it
