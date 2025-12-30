"""Add Trout Shore Fishing (TSF) competition models.

Revision ID: 20251229_100002
Revises: 20251229_100001
Create Date: 2025-12-29 10:00:02

TSF competitions use multi-day positional scoring:
- Competition spans multiple days (configurable)
- Participants divided into sectors/groups
- Position-based scoring (lower = better)
- Daily rankings + final overall ranking
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "20251229_100002"
down_revision = "20251229_100001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # TSF Event Settings
    # =========================================================================
    op.create_table(
        "tsf_event_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        # Day configuration
        sa.Column("number_of_days", sa.Integer(), nullable=False, default=2),
        # Sector configuration
        sa.Column("number_of_sectors", sa.Integer(), nullable=False, default=4),
        sa.Column("participants_per_sector", sa.Integer(), nullable=True),
        # Leg configuration
        sa.Column("legs_per_day", sa.Integer(), nullable=False, default=4),
        # Scoring
        sa.Column("scoring_direction", sa.String(length=10), nullable=False, default="lower"),
        sa.Column("ghost_position_penalty", sa.Integer(), nullable=False, default=0),
        sa.Column("tiebreaker_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        # Rotation
        sa.Column("rotate_sectors_daily", sa.Boolean(), nullable=False, default=True),
        sa.Column("seat_rotation_pattern", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # Additional
        sa.Column("additional_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False, default={}),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(op.f("ix_tsf_event_settings_id"), "tsf_event_settings", ["id"], unique=False)
    op.create_index(op.f("ix_tsf_event_settings_event_id"), "tsf_event_settings", ["event_id"], unique=True)

    # =========================================================================
    # TSF Days
    # =========================================================================
    op.create_table(
        "tsf_days",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, default="scheduled"),
        sa.Column("weather_conditions", sa.String(length=200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "day_number", name="uq_tsf_day_number"),
        sa.CheckConstraint("day_number >= 1", name="ck_tsf_day_positive"),
    )
    op.create_index(op.f("ix_tsf_days_id"), "tsf_days", ["id"], unique=False)
    op.create_index(op.f("ix_tsf_days_event_id"), "tsf_days", ["event_id"], unique=False)

    # =========================================================================
    # TSF Legs
    # =========================================================================
    op.create_table(
        "tsf_legs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("day_id", sa.Integer(), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("leg_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, default="scheduled"),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["day_id"], ["tsf_days.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "day_number", "leg_number", name="uq_tsf_leg"),
        sa.CheckConstraint("day_number >= 1", name="ck_tsf_leg_day_positive"),
        sa.CheckConstraint("leg_number >= 1", name="ck_tsf_leg_number_positive"),
    )
    op.create_index(op.f("ix_tsf_legs_id"), "tsf_legs", ["id"], unique=False)
    op.create_index(op.f("ix_tsf_legs_event_id"), "tsf_legs", ["event_id"], unique=False)
    op.create_index(op.f("ix_tsf_legs_day_id"), "tsf_legs", ["day_id"], unique=False)

    # =========================================================================
    # TSF Lineups (Group Assignments)
    # =========================================================================
    op.create_table(
        "tsf_lineups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("enrollment_id", sa.Integer(), nullable=True),
        sa.Column("draw_number", sa.Integer(), nullable=False),
        sa.Column("group_number", sa.Integer(), nullable=False),
        sa.Column("seat_index", sa.Integer(), nullable=False),
        sa.Column("is_ghost", sa.Boolean(), nullable=False, default=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enrollment_id"], ["event_enrollments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "user_id", name="uq_tsf_lineup_user"),
        sa.UniqueConstraint("event_id", "draw_number", name="uq_tsf_lineup_draw"),
        sa.CheckConstraint("group_number >= 1", name="ck_tsf_lineup_group_positive"),
        sa.CheckConstraint("seat_index >= 1", name="ck_tsf_lineup_seat_positive"),
    )
    op.create_index(op.f("ix_tsf_lineups_id"), "tsf_lineups", ["id"], unique=False)
    op.create_index(op.f("ix_tsf_lineups_event_id"), "tsf_lineups", ["event_id"], unique=False)
    op.create_index(op.f("ix_tsf_lineups_user_id"), "tsf_lineups", ["user_id"], unique=False)

    # =========================================================================
    # TSF Leg Positions
    # =========================================================================
    op.create_table(
        "tsf_leg_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("leg_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("group_number", sa.Integer(), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("leg_number", sa.Integer(), nullable=False),
        sa.Column("seat_index", sa.Integer(), nullable=False),
        sa.Column("position_value", sa.Integer(), nullable=False),
        sa.Column("fish_count", sa.Integer(), nullable=True),
        sa.Column("total_length", sa.Float(), nullable=True),
        sa.Column("best_checksum", sa.Integer(), nullable=True),
        sa.Column("worst_checksum", sa.Integer(), nullable=True),
        sa.Column("running_total", sa.Integer(), nullable=True),
        sa.Column("is_ghost", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_dnf", sa.Boolean(), nullable=False, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["leg_id"], ["tsf_legs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "leg_id", "user_id", name="uq_tsf_leg_position_user"),
        sa.UniqueConstraint("event_id", "leg_id", "group_number", "position_value", name="uq_tsf_leg_position_rank"),
        sa.CheckConstraint("position_value >= 1", name="ck_tsf_position_positive"),
    )
    op.create_index(op.f("ix_tsf_leg_positions_id"), "tsf_leg_positions", ["id"], unique=False)
    op.create_index(op.f("ix_tsf_leg_positions_event_id"), "tsf_leg_positions", ["event_id"], unique=False)
    op.create_index(op.f("ix_tsf_leg_positions_leg_id"), "tsf_leg_positions", ["leg_id"], unique=False)

    # =========================================================================
    # TSF Day Standings
    # =========================================================================
    op.create_table(
        "tsf_day_standings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("day_id", sa.Integer(), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("group_number", sa.Integer(), nullable=False),
        sa.Column("total_position_points", sa.Integer(), nullable=False, default=0),
        sa.Column("legs_completed", sa.Integer(), nullable=False, default=0),
        sa.Column("first_places", sa.Integer(), nullable=False, default=0),
        sa.Column("second_places", sa.Integer(), nullable=False, default=0),
        sa.Column("third_places", sa.Integer(), nullable=False, default=0),
        sa.Column("best_single_leg", sa.Integer(), nullable=True),
        sa.Column("worst_single_leg", sa.Integer(), nullable=True),
        sa.Column("total_fish_count", sa.Integer(), nullable=False, default=0),
        sa.Column("total_length", sa.Float(), nullable=False, default=0.0),
        sa.Column("sector_rank", sa.Integer(), nullable=True),
        sa.Column("overall_rank", sa.Integer(), nullable=True),
        sa.Column("leg_positions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, default={}),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["day_id"], ["tsf_days.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "day_id", "user_id", name="uq_tsf_day_standing"),
    )
    op.create_index(op.f("ix_tsf_day_standings_id"), "tsf_day_standings", ["id"], unique=False)
    op.create_index(op.f("ix_tsf_day_standings_event_id"), "tsf_day_standings", ["event_id"], unique=False)
    op.create_index(op.f("ix_tsf_day_standings_day_id"), "tsf_day_standings", ["day_id"], unique=False)

    # =========================================================================
    # TSF Final Standings
    # =========================================================================
    op.create_table(
        "tsf_final_standings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("enrollment_id", sa.Integer(), nullable=False),
        sa.Column("group_number", sa.Integer(), nullable=False),
        # Totals
        sa.Column("total_position_points", sa.Integer(), nullable=False, default=0),
        sa.Column("days_completed", sa.Integer(), nullable=False, default=0),
        sa.Column("legs_completed", sa.Integer(), nullable=False, default=0),
        # Placements
        sa.Column("total_first_places", sa.Integer(), nullable=False, default=0),
        sa.Column("total_second_places", sa.Integer(), nullable=False, default=0),
        sa.Column("total_third_places", sa.Integer(), nullable=False, default=0),
        # Best/worst
        sa.Column("best_single_leg", sa.Integer(), nullable=True),
        sa.Column("worst_single_leg", sa.Integer(), nullable=True),
        sa.Column("best_day_total", sa.Integer(), nullable=True),
        sa.Column("worst_day_total", sa.Integer(), nullable=True),
        # Fish stats
        sa.Column("total_fish_count", sa.Integer(), nullable=False, default=0),
        sa.Column("total_length", sa.Float(), nullable=False, default=0.0),
        # Final rank
        sa.Column("final_rank", sa.Integer(), nullable=True),
        # Day breakdown
        sa.Column("day_totals", postgresql.JSONB(astext_type=sa.Text()), nullable=False, default={}),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enrollment_id"], ["event_enrollments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "user_id", name="uq_tsf_final_standing"),
    )
    op.create_index(op.f("ix_tsf_final_standings_id"), "tsf_final_standings", ["id"], unique=False)
    op.create_index(op.f("ix_tsf_final_standings_event_id"), "tsf_final_standings", ["event_id"], unique=False)
    op.create_index(op.f("ix_tsf_final_standings_final_rank"), "tsf_final_standings", ["final_rank"], unique=False)


def downgrade() -> None:
    op.drop_table("tsf_final_standings")
    op.drop_table("tsf_day_standings")
    op.drop_table("tsf_leg_positions")
    op.drop_table("tsf_lineups")
    op.drop_table("tsf_legs")
    op.drop_table("tsf_days")
    op.drop_table("tsf_event_settings")
