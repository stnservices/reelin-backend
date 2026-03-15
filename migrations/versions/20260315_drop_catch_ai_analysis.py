"""Drop catch_ai_analysis table — AI analysis (Google Vision) removed.

Revision ID: drop_ai_analysis_001
Revises: forecast_queries_001
Create Date: 2026-03-15
"""

from typing import Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'drop_ai_analysis_001'
down_revision: Union[str, None] = 'forecast_queries_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("idx_ai_analysis_anomaly", table_name="catch_ai_analysis")
    op.drop_index("idx_ai_analysis_status", table_name="catch_ai_analysis")
    op.drop_index("idx_ai_analysis_catch", table_name="catch_ai_analysis")
    op.drop_table("catch_ai_analysis")


def downgrade() -> None:
    op.create_table(
        "catch_ai_analysis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("catch_id", sa.Integer(), nullable=False),
        sa.Column("detected_species_id", sa.Integer(), nullable=True),
        sa.Column("species_confidence", sa.Float(), nullable=True),
        sa.Column("species_alternatives", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("anomaly_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("anomaly_flags", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("metadata_warnings", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("raw_vision_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("perceptual_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # ML auto-validation fields (added in 20260112)
        sa.Column("validation_confidence", sa.Float(), nullable=True),
        sa.Column("validation_recommendation", sa.String(20), nullable=True),
        sa.Column("ai_insights", sa.Text(), nullable=True),
        sa.Column("auto_validated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("auto_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["catch_id"], ["catches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["detected_species_id"], ["fish.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("catch_id", name="uq_catch_ai_analysis_catch_id"),
    )
    op.create_index("idx_ai_analysis_catch", "catch_ai_analysis", ["catch_id"], unique=False)
    op.create_index("idx_ai_analysis_status", "catch_ai_analysis", ["status"], unique=False)
    op.create_index("idx_ai_analysis_anomaly", "catch_ai_analysis", [sa.text("anomaly_score DESC")], unique=False)
