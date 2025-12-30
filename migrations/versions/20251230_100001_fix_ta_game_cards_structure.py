"""Fix TA Game Cards structure to per-user, per-leg model.

Revision ID: 20251230_100001
Revises: 20251229_190000
Create Date: 2025-12-30 10:00:01

This migration:
1. Drops the old ta_game_cards table (per-match structure)
2. Creates new ta_game_cards table (per-user, per-leg structure)
3. Adds team_id to ta_lineups for team event support
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "20251230_100001"
down_revision = "20251229_190000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # Drop old ta_game_cards table (per-match structure)
    # =========================================================================
    op.drop_index("ix_ta_game_cards_match_id", table_name="ta_game_cards")
    op.drop_index("ix_ta_game_cards_id", table_name="ta_game_cards")
    op.drop_table("ta_game_cards")

    # =========================================================================
    # Create new ta_game_cards table (per-user, per-leg structure)
    # =========================================================================
    op.create_table(
        "ta_game_cards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("leg_number", sa.Integer(), nullable=False),
        # Card owner
        sa.Column("user_id", sa.Integer(), nullable=False),
        # User's entry (what this user caught)
        sa.Column("my_catches", sa.Integer(), nullable=True),
        sa.Column("my_seat", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        # Opponent info (for display - populated from opponent's card)
        sa.Column("opponent_id", sa.Integer(), nullable=True),
        sa.Column("opponent_catches", sa.Integer(), nullable=True),
        sa.Column("opponent_seat", sa.Integer(), nullable=True),
        # Submission & Validation status
        sa.Column("is_submitted", sa.Boolean(), nullable=False, server_default="false"),
        # Was MY catches validated by opponent?
        sa.Column("is_validated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("validated_by_id", sa.Integer(), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        # Did I validate opponent's catches?
        sa.Column("i_validated_opponent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("i_validated_at", sa.DateTime(timezone=True), nullable=True),
        # Dispute
        sa.Column("is_disputed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("dispute_reason", sa.Text(), nullable=True),
        sa.Column("dispute_resolved_by_id", sa.Integer(), nullable=True),
        sa.Column("dispute_resolved_at", sa.DateTime(timezone=True), nullable=True),
        # Card status
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        # Ghost opponent flag
        sa.Column("is_ghost_opponent", sa.Boolean(), nullable=False, server_default="false"),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # Foreign keys
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["match_id"], ["ta_matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["opponent_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["validated_by_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["dispute_resolved_by_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        # Each user has one card per leg per event
        sa.UniqueConstraint("event_id", "leg_number", "user_id", name="uq_ta_game_card_user_leg"),
    )
    op.create_index("ix_ta_game_cards_id", "ta_game_cards", ["id"], unique=False)
    op.create_index("ix_ta_game_cards_event_id", "ta_game_cards", ["event_id"], unique=False)
    op.create_index("ix_ta_game_cards_match_id", "ta_game_cards", ["match_id"], unique=False)
    op.create_index("ix_ta_game_cards_user_id", "ta_game_cards", ["user_id"], unique=False)

    # =========================================================================
    # Add team_id to ta_lineups for team event support
    # =========================================================================
    op.add_column(
        "ta_lineups",
        sa.Column("team_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_ta_lineups_team_id",
        "ta_lineups",
        "teams",
        ["team_id"],
        ["id"],
        ondelete="SET NULL"
    )
    op.create_index("ix_ta_lineups_team_id", "ta_lineups", ["team_id"], unique=False)


def downgrade() -> None:
    # Remove team_id from ta_lineups
    op.drop_index("ix_ta_lineups_team_id", table_name="ta_lineups")
    op.drop_constraint("fk_ta_lineups_team_id", "ta_lineups", type_="foreignkey")
    op.drop_column("ta_lineups", "team_id")

    # Drop new ta_game_cards table
    op.drop_index("ix_ta_game_cards_user_id", table_name="ta_game_cards")
    op.drop_index("ix_ta_game_cards_match_id", table_name="ta_game_cards")
    op.drop_index("ix_ta_game_cards_event_id", table_name="ta_game_cards")
    op.drop_index("ix_ta_game_cards_id", table_name="ta_game_cards")
    op.drop_table("ta_game_cards")

    # Recreate old ta_game_cards table (per-match structure)
    op.create_table(
        "ta_game_cards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        # Competitor A data
        sa.Column("competitor_a_catches", sa.Integer(), nullable=True),
        sa.Column("competitor_a_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("competitor_a_validated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("competitor_a_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("competitor_a_notes", sa.Text(), nullable=True),
        # Competitor B data
        sa.Column("competitor_b_catches", sa.Integer(), nullable=True),
        sa.Column("competitor_b_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("competitor_b_validated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("competitor_b_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("competitor_b_notes", sa.Text(), nullable=True),
        # Status
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        # Dispute
        sa.Column("has_dispute", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("dispute_reason", sa.Text(), nullable=True),
        sa.Column("dispute_resolved_by_id", sa.Integer(), nullable=True),
        sa.Column("dispute_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispute_resolution_notes", sa.Text(), nullable=True),
        # Timestamps
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["ta_matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dispute_resolved_by_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id"),
    )
    op.create_index("ix_ta_game_cards_id", "ta_game_cards", ["id"], unique=False)
    op.create_index("ix_ta_game_cards_match_id", "ta_game_cards", ["match_id"], unique=True)
