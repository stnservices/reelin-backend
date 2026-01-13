"""Add Hall of Fame table and Top Anglers ranking view.

Revision ID: hall_of_fame_and_top_anglers
Revises: 401ccf68f1a0
Create Date: 2026-01-13

This migration adds:
1. hall_of_fame_entries table for external achievements (world championships, etc.)
2. top_anglers_ranking SQL view for calculating Top 10 anglers from national events
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'hall_of_fame_and_top_anglers'
down_revision: Union[str, None] = '401ccf68f1a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =========================================================================
    # Step 1: Create hall_of_fame_entries table
    # =========================================================================
    op.create_table(
        'hall_of_fame_entries',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user_accounts.id', ondelete='SET NULL'), nullable=True),
        sa.Column('athlete_name', sa.String(255), nullable=False),
        sa.Column('athlete_avatar_url', sa.String(500), nullable=True),
        sa.Column('achievement_type', sa.String(50), nullable=False),
        sa.Column('competition_name', sa.String(255), nullable=False),
        sa.Column('competition_year', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=True),
        sa.Column('format_code', sa.String(10), nullable=True),
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column('country', sa.String(100), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('image_url', sa.String(500), nullable=True),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user_accounts.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    # Create indexes for hall_of_fame_entries
    op.create_index('ix_hall_of_fame_entries_user_id', 'hall_of_fame_entries', ['user_id'])
    op.create_index('ix_hall_of_fame_entries_achievement_type', 'hall_of_fame_entries', ['achievement_type'])
    op.create_index('ix_hall_of_fame_entries_competition_year', 'hall_of_fame_entries', ['competition_year'])
    op.create_index('ix_hall_of_fame_entries_format_code', 'hall_of_fame_entries', ['format_code'])

    # =========================================================================
    # Step 2: Create top_anglers_ranking SQL view
    # =========================================================================
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

    # =========================================================================
    # Step 3: Create an all-time view (aggregated across years)
    # =========================================================================
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
    # =========================================================================
    # Reverse Step 3: Drop all-time view
    # =========================================================================
    op.execute("DROP VIEW IF EXISTS top_anglers_all_time;")

    # =========================================================================
    # Reverse Step 2: Drop top_anglers_ranking view
    # =========================================================================
    op.execute("DROP VIEW IF EXISTS top_anglers_ranking;")

    # =========================================================================
    # Reverse Step 1: Drop hall_of_fame_entries table and indexes
    # =========================================================================
    op.drop_index('ix_hall_of_fame_entries_format_code', table_name='hall_of_fame_entries')
    op.drop_index('ix_hall_of_fame_entries_competition_year', table_name='hall_of_fame_entries')
    op.drop_index('ix_hall_of_fame_entries_achievement_type', table_name='hall_of_fame_entries')
    op.drop_index('ix_hall_of_fame_entries_user_id', table_name='hall_of_fame_entries')
    op.drop_table('hall_of_fame_entries')
