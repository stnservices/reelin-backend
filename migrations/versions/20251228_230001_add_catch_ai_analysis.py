"""Add catch_ai_analysis table for AI-powered catch validation hints.

Revision ID: 20251228_230001
Revises: 20251228_220001
Create Date: 2025-12-28 23:00:01

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20251228_230001"
down_revision = "20251228_220001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the catch_ai_analysis table
    op.create_table(
        "catch_ai_analysis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("catch_id", sa.Integer(), nullable=False),
        # Species detection
        sa.Column("detected_species_id", sa.Integer(), nullable=True),
        sa.Column("species_confidence", sa.Float(), nullable=True),
        sa.Column(
            "species_alternatives",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        # Anomaly detection
        sa.Column("anomaly_score", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "anomaly_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        # Metadata analysis
        sa.Column(
            "metadata_warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        # Raw data for debugging
        sa.Column(
            "raw_vision_response",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "raw_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        # Perceptual hash for image similarity
        sa.Column("perceptual_hash", sa.String(64), nullable=True),
        # Processing info
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["catch_id"],
            ["catches.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["detected_species_id"],
            ["fish.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("catch_id", name="uq_catch_ai_analysis_catch_id"),
    )

    # Create indexes
    op.create_index(
        "idx_ai_analysis_catch", "catch_ai_analysis", ["catch_id"], unique=False
    )
    op.create_index(
        "idx_ai_analysis_status", "catch_ai_analysis", ["status"], unique=False
    )
    op.create_index(
        "idx_ai_analysis_anomaly",
        "catch_ai_analysis",
        [sa.text("anomaly_score DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_ai_analysis_anomaly", table_name="catch_ai_analysis")
    op.drop_index("idx_ai_analysis_status", table_name="catch_ai_analysis")
    op.drop_index("idx_ai_analysis_catch", table_name="catch_ai_analysis")
    op.drop_table("catch_ai_analysis")
