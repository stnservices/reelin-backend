"""Add Trout Area (TA) competition models.

Revision ID: 20251229_100001
Revises: 20251228_240001
Create Date: 2025-12-29 10:00:01

TA competitions use head-to-head match-based scoring with:
- Qualifier phase (legs with rotating seats)
- Knockout phase (requalification, semifinals, finals)
- Self-validation between competitors
- Game cards for match recording
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "20251229_100001"
down_revision = "20251228_240001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # TA Points Rules
    # =========================================================================
    op.create_table(
        "ta_points_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=5), nullable=False),
        sa.Column("points", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index(op.f("ix_ta_points_rules_id"), "ta_points_rules", ["id"], unique=False)

    # =========================================================================
    # TA Event Settings
    # =========================================================================
    op.create_table(
        "ta_event_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        # Qualifier settings
        sa.Column("number_of_legs", sa.Integer(), nullable=False, default=5),
        sa.Column("max_rounds_per_leg", sa.Integer(), nullable=False, default=1),
        # Knockout settings
        sa.Column("has_knockout_stage", sa.Boolean(), nullable=False, default=True),
        sa.Column("knockout_qualifiers", sa.Integer(), nullable=False, default=6),
        sa.Column("has_requalification", sa.Boolean(), nullable=False, default=True),
        sa.Column("requalification_slots", sa.Integer(), nullable=False, default=4),
        sa.Column("direct_placement_from", sa.Integer(), nullable=False, default=7),
        # Team settings
        sa.Column("is_team_event", sa.Boolean(), nullable=False, default=False),
        sa.Column("team_size", sa.Integer(), nullable=True),
        sa.Column("team_scoring_method", sa.String(length=50), nullable=True),
        # Validation settings
        sa.Column("require_both_validation", sa.Boolean(), nullable=False, default=True),
        sa.Column("auto_validate_ghost", sa.Boolean(), nullable=False, default=True),
        sa.Column("dispute_resolution_timeout_hours", sa.Integer(), nullable=False, default=24),
        # Match settings
        sa.Column("match_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("break_between_legs_minutes", sa.Integer(), nullable=True),
        # Additional rules
        sa.Column("additional_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(op.f("ix_ta_event_settings_id"), "ta_event_settings", ["id"], unique=False)
    op.create_index(op.f("ix_ta_event_settings_event_id"), "ta_event_settings", ["event_id"], unique=True)

    # =========================================================================
    # TA Lineups (Draw/Seat Assignments)
    # =========================================================================
    op.create_table(
        "ta_lineups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("leg_number", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("enrollment_id", sa.Integer(), nullable=True),
        sa.Column("draw_number", sa.Integer(), nullable=False),
        sa.Column("sector", sa.Integer(), nullable=False),
        sa.Column("seat_number", sa.Integer(), nullable=False),
        sa.Column("is_ghost", sa.Boolean(), nullable=False, default=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enrollment_id"], ["event_enrollments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "leg_number", "draw_number", name="uq_ta_lineup_draw"),
        sa.UniqueConstraint("event_id", "leg_number", "seat_number", name="uq_ta_lineup_seat"),
        sa.CheckConstraint("leg_number >= 1", name="ck_ta_lineup_leg_positive"),
        sa.CheckConstraint("draw_number >= 1", name="ck_ta_lineup_draw_positive"),
        sa.CheckConstraint("sector >= 1", name="ck_ta_lineup_sector_positive"),
    )
    op.create_index(op.f("ix_ta_lineups_id"), "ta_lineups", ["id"], unique=False)
    op.create_index(op.f("ix_ta_lineups_event_id"), "ta_lineups", ["event_id"], unique=False)
    op.create_index(op.f("ix_ta_lineups_user_id"), "ta_lineups", ["user_id"], unique=False)

    # =========================================================================
    # TA Matches
    # =========================================================================
    op.create_table(
        "ta_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=20), nullable=False, default="qualifier"),
        sa.Column("leg_number", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False, default=1),
        sa.Column("match_number", sa.Integer(), nullable=False),
        # Seats
        sa.Column("seat_a", sa.Integer(), nullable=False),
        sa.Column("seat_b", sa.Integer(), nullable=False),
        # Competitor A
        sa.Column("competitor_a_id", sa.Integer(), nullable=True),
        sa.Column("competitor_a_enrollment_id", sa.Integer(), nullable=True),
        sa.Column("competitor_a_draw_number", sa.Integer(), nullable=True),
        sa.Column("competitor_a_catches", sa.Integer(), nullable=True),
        sa.Column("competitor_a_points", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("competitor_a_outcome_code", sa.String(length=5), nullable=True),
        sa.Column("is_valid_a", sa.Boolean(), nullable=False, default=False),
        # Competitor B
        sa.Column("competitor_b_id", sa.Integer(), nullable=True),
        sa.Column("competitor_b_enrollment_id", sa.Integer(), nullable=True),
        sa.Column("competitor_b_draw_number", sa.Integer(), nullable=True),
        sa.Column("competitor_b_catches", sa.Integer(), nullable=True),
        sa.Column("competitor_b_points", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("competitor_b_outcome_code", sa.String(length=5), nullable=True),
        sa.Column("is_valid_b", sa.Boolean(), nullable=False, default=False),
        # Ghost
        sa.Column("is_ghost_match", sa.Boolean(), nullable=False, default=False),
        sa.Column("ghost_side", sa.String(length=1), nullable=True),
        # Status
        sa.Column("status", sa.String(length=20), nullable=False, default="scheduled"),
        # Timestamps
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["competitor_a_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["competitor_b_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["competitor_a_enrollment_id"], ["event_enrollments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["competitor_b_enrollment_id"], ["event_enrollments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "phase", "leg_number", "round_number", "match_number", name="uq_ta_match_position"),
        sa.CheckConstraint("leg_number >= 1", name="ck_ta_match_leg_positive"),
        sa.CheckConstraint("round_number >= 1", name="ck_ta_match_round_positive"),
        sa.CheckConstraint("seat_a >= 1", name="ck_ta_match_seat_a_positive"),
        sa.CheckConstraint("seat_b >= 1", name="ck_ta_match_seat_b_positive"),
    )
    op.create_index(op.f("ix_ta_matches_id"), "ta_matches", ["id"], unique=False)
    op.create_index(op.f("ix_ta_matches_event_id"), "ta_matches", ["event_id"], unique=False)
    op.create_index(op.f("ix_ta_matches_status"), "ta_matches", ["status"], unique=False)
    op.create_index(op.f("ix_ta_matches_competitor_a_id"), "ta_matches", ["competitor_a_id"], unique=False)
    op.create_index(op.f("ix_ta_matches_competitor_b_id"), "ta_matches", ["competitor_b_id"], unique=False)

    # =========================================================================
    # TA Game Cards
    # =========================================================================
    op.create_table(
        "ta_game_cards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        # Competitor A data
        sa.Column("competitor_a_catches", sa.Integer(), nullable=True),
        sa.Column("competitor_a_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("competitor_a_validated", sa.Boolean(), nullable=False, default=False),
        sa.Column("competitor_a_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("competitor_a_notes", sa.Text(), nullable=True),
        # Competitor B data
        sa.Column("competitor_b_catches", sa.Integer(), nullable=True),
        sa.Column("competitor_b_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("competitor_b_validated", sa.Boolean(), nullable=False, default=False),
        sa.Column("competitor_b_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("competitor_b_notes", sa.Text(), nullable=True),
        # Status
        sa.Column("status", sa.String(length=20), nullable=False, default="draft"),
        # Dispute
        sa.Column("has_dispute", sa.Boolean(), nullable=False, default=False),
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
    op.create_index(op.f("ix_ta_game_cards_id"), "ta_game_cards", ["id"], unique=False)
    op.create_index(op.f("ix_ta_game_cards_match_id"), "ta_game_cards", ["match_id"], unique=True)

    # =========================================================================
    # TA Knockout Brackets
    # =========================================================================
    op.create_table(
        "ta_knockout_brackets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("total_qualifiers", sa.Integer(), nullable=False),
        sa.Column("seeds", postgresql.JSONB(astext_type=sa.Text()), nullable=False, default={}),
        sa.Column("direct_placements", postgresql.JSONB(astext_type=sa.Text()), nullable=False, default={}),
        sa.Column("is_generated", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_completed", sa.Boolean(), nullable=False, default=False),
        sa.Column("final_standings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(op.f("ix_ta_knockout_brackets_id"), "ta_knockout_brackets", ["id"], unique=False)
    op.create_index(op.f("ix_ta_knockout_brackets_event_id"), "ta_knockout_brackets", ["event_id"], unique=True)

    # =========================================================================
    # TA Knockout Matches
    # =========================================================================
    op.create_table(
        "ta_knockout_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bracket_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=20), nullable=False),
        sa.Column("match_number", sa.Integer(), nullable=False),
        # Advancement
        sa.Column("winner_advances_to_phase", sa.String(length=20), nullable=True),
        sa.Column("winner_advances_to_match", sa.Integer(), nullable=True),
        sa.Column("loser_advances_to_phase", sa.String(length=20), nullable=True),
        sa.Column("loser_advances_to_match", sa.Integer(), nullable=True),
        sa.Column("winner_placement", sa.Integer(), nullable=True),
        sa.Column("loser_placement", sa.Integer(), nullable=True),
        # Competitors
        sa.Column("competitor_a_id", sa.Integer(), nullable=True),
        sa.Column("competitor_a_seed", sa.Integer(), nullable=True),
        sa.Column("competitor_a_catches", sa.Integer(), nullable=True),
        sa.Column("competitor_a_is_winner", sa.Boolean(), nullable=True),
        sa.Column("competitor_b_id", sa.Integer(), nullable=True),
        sa.Column("competitor_b_seed", sa.Integer(), nullable=True),
        sa.Column("competitor_b_catches", sa.Integer(), nullable=True),
        sa.Column("competitor_b_is_winner", sa.Boolean(), nullable=True),
        # Status
        sa.Column("status", sa.String(length=20), nullable=False, default="scheduled"),
        # Timestamps
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["bracket_id"], ["ta_knockout_brackets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["competitor_a_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["competitor_b_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bracket_id", "phase", "match_number", name="uq_ta_knockout_match"),
    )
    op.create_index(op.f("ix_ta_knockout_matches_id"), "ta_knockout_matches", ["id"], unique=False)
    op.create_index(op.f("ix_ta_knockout_matches_bracket_id"), "ta_knockout_matches", ["bracket_id"], unique=False)
    op.create_index(op.f("ix_ta_knockout_matches_event_id"), "ta_knockout_matches", ["event_id"], unique=False)

    # =========================================================================
    # TA Qualifier Standings
    # =========================================================================
    op.create_table(
        "ta_qualifier_standings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("enrollment_id", sa.Integer(), nullable=False),
        sa.Column("total_points", sa.Numeric(precision=10, scale=2), nullable=False, default=0),
        sa.Column("total_matches", sa.Integer(), nullable=False, default=0),
        sa.Column("total_victories", sa.Integer(), nullable=False, default=0),
        sa.Column("total_ties", sa.Integer(), nullable=False, default=0),
        sa.Column("total_losses", sa.Integer(), nullable=False, default=0),
        sa.Column("total_fish_caught", sa.Integer(), nullable=False, default=0),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("qualifies_for_knockout", sa.Boolean(), nullable=False, default=False),
        sa.Column("leg_results", postgresql.JSONB(astext_type=sa.Text()), nullable=False, default={}),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enrollment_id"], ["event_enrollments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "user_id", name="uq_ta_standing_user"),
    )
    op.create_index(op.f("ix_ta_qualifier_standings_id"), "ta_qualifier_standings", ["id"], unique=False)
    op.create_index(op.f("ix_ta_qualifier_standings_event_id"), "ta_qualifier_standings", ["event_id"], unique=False)
    op.create_index(op.f("ix_ta_qualifier_standings_user_id"), "ta_qualifier_standings", ["user_id"], unique=False)
    op.create_index(op.f("ix_ta_qualifier_standings_rank"), "ta_qualifier_standings", ["rank"], unique=False)


def downgrade() -> None:
    op.drop_table("ta_qualifier_standings")
    op.drop_table("ta_knockout_matches")
    op.drop_table("ta_knockout_brackets")
    op.drop_table("ta_game_cards")
    op.drop_table("ta_matches")
    op.drop_table("ta_lineups")
    op.drop_table("ta_event_settings")
    op.drop_table("ta_points_rules")
