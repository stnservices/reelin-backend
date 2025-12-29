"""Add ML models and prediction logs tables.

Revision ID: 20251228_240001
Revises: 20251228_230001
Create Date: 2025-12-28 24:00:01

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20251228_240001"
down_revision = "20251228_230001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create ml_models table
    op.create_table(
        "ml_models",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "model_type",
            sa.String(50),
            nullable=False,
        ),  # event_recommendations, analytics_predictions
        sa.Column("file_path", sa.String(255), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        # Training metadata
        sa.Column(
            "trained_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("training_samples", sa.Integer(), nullable=True),
        sa.Column("positive_rate", sa.Float(), nullable=True),
        sa.Column("roc_auc", sa.Float(), nullable=True),
        sa.Column("cv_roc_auc", sa.Float(), nullable=True),
        sa.Column(
            "feature_columns",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "feature_importance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        # Runtime stats
        sa.Column(
            "predictions_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("last_prediction_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("avg_prediction_ms", sa.Float(), nullable=True),
        # Admin
        sa.Column("uploaded_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["uploaded_by_id"],
            ["user_accounts.id"],
            ondelete="SET NULL",
        ),
    )

    # Create indexes for ml_models
    op.create_index(
        "idx_ml_models_type", "ml_models", ["model_type"], unique=False
    )
    op.create_index(
        "idx_ml_models_active", "ml_models", ["is_active"], unique=False
    )

    # Create ml_prediction_logs table
    op.create_table(
        "ml_prediction_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("entity_type", sa.String(50), nullable=False),  # event, angler
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("prediction_score", sa.Float(), nullable=False),
        sa.Column(
            "actual_outcome", sa.Boolean(), nullable=True
        ),  # Filled later when we know if user enrolled
        sa.Column("prediction_ms", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["ml_models.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_accounts.id"],
            ondelete="SET NULL",
        ),
    )

    # Create indexes for prediction logs
    op.create_index(
        "idx_prediction_logs_model_date",
        "ml_prediction_logs",
        ["model_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_prediction_logs_user",
        "ml_prediction_logs",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_prediction_logs_user", table_name="ml_prediction_logs")
    op.drop_index("idx_prediction_logs_model_date", table_name="ml_prediction_logs")
    op.drop_table("ml_prediction_logs")

    op.drop_index("idx_ml_models_active", table_name="ml_models")
    op.drop_index("idx_ml_models_type", table_name="ml_models")
    op.drop_table("ml_models")
