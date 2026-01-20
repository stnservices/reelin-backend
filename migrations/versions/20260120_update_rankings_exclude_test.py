"""Update rankings views to exclude test events.

Revision ID: update_rankings_exclude_test
Revises: add_is_test_to_events
Create Date: 2026-01-20

This migration updates the top_anglers_ranking view to exclude test events.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'update_rankings_exclude_test'
down_revision: Union[str, None] = 'add_is_test_to_events'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Update top_anglers_ranking view to exclude test events."""
    # Drop the all-time view first (it depends on the ranking view)
    op.execute("DROP VIEW IF EXISTS top_anglers_all_time;")

    # Drop and recreate the ranking view with is_test filter
    op.execute("DROP VIEW IF EXISTS top_anglers_ranking;")

    op.execute("""
        CREATE OR REPLACE VIEW top_anglers_ranking AS
        WITH national_results AS (
            SELECT
                es.user_id,
                e.id as event_id,
                e.event_type_id,
                et.format_code,
                EXTRACT(YEAR FROM e.start_date)::integer as competition_year,
                es.rank as position,
                es.total_length,
                es.total_catches,
                es.total_points,
                es.best_catch_length,
                CASE
                    WHEN es.rank = 1 THEN 100
                    WHEN es.rank = 2 THEN 85
                    WHEN es.rank = 3 THEN 70
                    WHEN es.rank = 4 THEN 60
                    WHEN es.rank = 5 THEN 50
                    WHEN es.rank = 6 THEN 42
                    WHEN es.rank = 7 THEN 36
                    WHEN es.rank = 8 THEN 30
                    WHEN es.rank = 9 THEN 25
                    WHEN es.rank = 10 THEN 20
                    WHEN es.rank BETWEEN 11 AND 20 THEN 20 - (es.rank - 10)
                    ELSE 5
                END as position_points,
                CASE
                    WHEN es.rank = 1 THEN 25
                    WHEN es.rank = 2 THEN 15
                    WHEN es.rank = 3 THEN 10
                    ELSE 0
                END as podium_bonus
            FROM event_scoreboards es
            JOIN events e ON e.id = es.event_id
            JOIN event_types et ON et.id = e.event_type_id
            WHERE e.is_national_event = TRUE
              AND e.status = 'completed'
              AND e.is_test = FALSE
        )
        SELECT
            user_id,
            format_code,
            competition_year,
            COUNT(*)::integer as participations,
            SUM(position_points)::integer as total_position_points,
            SUM(podium_bonus)::integer as total_podium_bonus,
            (COUNT(*) * 3)::integer as participation_weight,
            (SUM(position_points) + SUM(podium_bonus) + (COUNT(*) * 3))::integer as total_score,
            -- Tiebreakers:
            COALESCE(SUM(total_points), 0)::float as total_leaderboard_points,
            COALESCE(AVG(total_catches), 0)::float as avg_catches_per_event,
            COALESCE(MAX(best_catch_length), 0)::float as best_single_catch,
            -- Medal counts:
            COUNT(*) FILTER (WHERE position = 1)::integer as gold_count,
            COUNT(*) FILTER (WHERE position = 2)::integer as silver_count,
            COUNT(*) FILTER (WHERE position = 3)::integer as bronze_count
        FROM national_results
        GROUP BY user_id, format_code, competition_year;
    """)

    # Recreate the all-time view
    op.execute("""
        CREATE OR REPLACE VIEW top_anglers_all_time AS
        SELECT
            user_id,
            format_code,
            NULL::integer as competition_year,
            SUM(participations)::integer as participations,
            SUM(total_position_points)::integer as total_position_points,
            SUM(total_podium_bonus)::integer as total_podium_bonus,
            SUM(participation_weight)::integer as participation_weight,
            SUM(total_score)::integer as total_score,
            SUM(total_leaderboard_points)::float as total_leaderboard_points,
            AVG(avg_catches_per_event)::float as avg_catches_per_event,
            MAX(best_single_catch)::float as best_single_catch,
            SUM(gold_count)::integer as gold_count,
            SUM(silver_count)::integer as silver_count,
            SUM(bronze_count)::integer as bronze_count
        FROM top_anglers_ranking
        GROUP BY user_id, format_code;
    """)


def downgrade() -> None:
    """Revert top_anglers_ranking view to include test events."""
    # Drop the all-time view first
    op.execute("DROP VIEW IF EXISTS top_anglers_all_time;")

    # Drop and recreate the ranking view without is_test filter
    op.execute("DROP VIEW IF EXISTS top_anglers_ranking;")

    op.execute("""
        CREATE OR REPLACE VIEW top_anglers_ranking AS
        WITH national_results AS (
            SELECT
                es.user_id,
                e.id as event_id,
                e.event_type_id,
                et.format_code,
                EXTRACT(YEAR FROM e.start_date)::integer as competition_year,
                es.rank as position,
                es.total_length,
                es.total_catches,
                es.total_points,
                es.best_catch_length,
                CASE
                    WHEN es.rank = 1 THEN 100
                    WHEN es.rank = 2 THEN 85
                    WHEN es.rank = 3 THEN 70
                    WHEN es.rank = 4 THEN 60
                    WHEN es.rank = 5 THEN 50
                    WHEN es.rank = 6 THEN 42
                    WHEN es.rank = 7 THEN 36
                    WHEN es.rank = 8 THEN 30
                    WHEN es.rank = 9 THEN 25
                    WHEN es.rank = 10 THEN 20
                    WHEN es.rank BETWEEN 11 AND 20 THEN 20 - (es.rank - 10)
                    ELSE 5
                END as position_points,
                CASE
                    WHEN es.rank = 1 THEN 25
                    WHEN es.rank = 2 THEN 15
                    WHEN es.rank = 3 THEN 10
                    ELSE 0
                END as podium_bonus
            FROM event_scoreboards es
            JOIN events e ON e.id = es.event_id
            JOIN event_types et ON et.id = e.event_type_id
            WHERE e.is_national_event = TRUE
              AND e.status = 'completed'
        )
        SELECT
            user_id,
            format_code,
            competition_year,
            COUNT(*)::integer as participations,
            SUM(position_points)::integer as total_position_points,
            SUM(podium_bonus)::integer as total_podium_bonus,
            (COUNT(*) * 3)::integer as participation_weight,
            (SUM(position_points) + SUM(podium_bonus) + (COUNT(*) * 3))::integer as total_score,
            -- Tiebreakers:
            COALESCE(SUM(total_points), 0)::float as total_leaderboard_points,
            COALESCE(AVG(total_catches), 0)::float as avg_catches_per_event,
            COALESCE(MAX(best_catch_length), 0)::float as best_single_catch,
            -- Medal counts:
            COUNT(*) FILTER (WHERE position = 1)::integer as gold_count,
            COUNT(*) FILTER (WHERE position = 2)::integer as silver_count,
            COUNT(*) FILTER (WHERE position = 3)::integer as bronze_count
        FROM national_results
        GROUP BY user_id, format_code, competition_year;
    """)

    # Recreate the all-time view
    op.execute("""
        CREATE OR REPLACE VIEW top_anglers_all_time AS
        SELECT
            user_id,
            format_code,
            NULL::integer as competition_year,
            SUM(participations)::integer as participations,
            SUM(total_position_points)::integer as total_position_points,
            SUM(total_podium_bonus)::integer as total_podium_bonus,
            SUM(participation_weight)::integer as participation_weight,
            SUM(total_score)::integer as total_score,
            SUM(total_leaderboard_points)::float as total_leaderboard_points,
            AVG(avg_catches_per_event)::float as avg_catches_per_event,
            MAX(best_single_catch)::float as best_single_catch,
            SUM(gold_count)::integer as gold_count,
            SUM(silver_count)::integer as silver_count,
            SUM(bronze_count)::integer as bronze_count
        FROM top_anglers_ranking
        GROUP BY user_id, format_code;
    """)
