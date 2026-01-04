"""Add tiebreaker columns to TAQualifierStanding.

Revision ID: 20260101_add_tiebreaker_cols
Revises: 20260101_add_xf_achievements
Create Date: 2026-01-01

Adds detailed outcome columns for full 7-tiebreaker ranking:
- ties_with_fish
- ties_without_fish
- losses_with_fish
- losses_without_fish
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260101_add_tiebreaker_cols"
down_revision = "20260101_add_xf_achievements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns for detailed tiebreaker tracking
    op.add_column(
        "ta_qualifier_standings",
        sa.Column("ties_with_fish", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ta_qualifier_standings",
        sa.Column("ties_without_fish", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ta_qualifier_standings",
        sa.Column("losses_with_fish", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ta_qualifier_standings",
        sa.Column("losses_without_fish", sa.Integer(), nullable=False, server_default="0"),
    )

    # Backfill existing data from ta_matches
    # For each standing, count the detailed outcomes from completed matches
    # Outcome codes: V=Victory, T=TieWithFish, T0=TieNoFish, L=LossWithFish, L0=LossNoFish
    op.execute("""
        WITH outcome_counts AS (
            SELECT
                m.event_id,
                m.competitor_a_id as user_id,
                COUNT(*) FILTER (WHERE m.competitor_a_outcome_code = 'T') as twf,
                COUNT(*) FILTER (WHERE m.competitor_a_outcome_code = 'T0') as twof,
                COUNT(*) FILTER (WHERE m.competitor_a_outcome_code = 'L') as lwf,
                COUNT(*) FILTER (WHERE m.competitor_a_outcome_code = 'L0') as lwof
            FROM ta_matches m
            WHERE m.status = 'completed' AND m.competitor_a_id IS NOT NULL
            GROUP BY m.event_id, m.competitor_a_id

            UNION ALL

            SELECT
                m.event_id,
                m.competitor_b_id as user_id,
                COUNT(*) FILTER (WHERE m.competitor_b_outcome_code = 'T') as twf,
                COUNT(*) FILTER (WHERE m.competitor_b_outcome_code = 'T0') as twof,
                COUNT(*) FILTER (WHERE m.competitor_b_outcome_code = 'L') as lwf,
                COUNT(*) FILTER (WHERE m.competitor_b_outcome_code = 'L0') as lwof
            FROM ta_matches m
            WHERE m.status = 'completed' AND m.competitor_b_id IS NOT NULL
            GROUP BY m.event_id, m.competitor_b_id
        ),
        aggregated AS (
            SELECT
                event_id,
                user_id,
                SUM(twf) as ties_with_fish,
                SUM(twof) as ties_without_fish,
                SUM(lwf) as losses_with_fish,
                SUM(lwof) as losses_without_fish
            FROM outcome_counts
            GROUP BY event_id, user_id
        )
        UPDATE ta_qualifier_standings s
        SET
            ties_with_fish = COALESCE(a.ties_with_fish, 0),
            ties_without_fish = COALESCE(a.ties_without_fish, 0),
            losses_with_fish = COALESCE(a.losses_with_fish, 0),
            losses_without_fish = COALESCE(a.losses_without_fish, 0)
        FROM aggregated a
        WHERE s.event_id = a.event_id AND s.user_id = a.user_id
    """)


def downgrade() -> None:
    op.drop_column("ta_qualifier_standings", "losses_without_fish")
    op.drop_column("ta_qualifier_standings", "losses_with_fish")
    op.drop_column("ta_qualifier_standings", "ties_without_fish")
    op.drop_column("ta_qualifier_standings", "ties_with_fish")
