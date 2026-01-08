"""Remove TSF (Trout Shore Fishing) completely.

Revision ID: remove_tsf_completely
Revises: remove_sponsor_tier
Create Date: 2026-01-08

This migration removes all TSF-related tables and columns:
- Drops 9 TSF tables (respecting foreign key order)
- Removes TSF columns from user_event_type_stats
- Removes TSF achievements from achievement_definitions
- Removes TSF event type if no events exist
- Removes TSF scoring configs

TSF is being deprecated and may be restored in the future.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'remove_tsf_completely'
down_revision: Union[str, None] = 'remove_sponsor_tier'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =========================================================================
    # Step 1: Check for existing TSF events (fail if any exist)
    # =========================================================================
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT COUNT(*) FROM events e
        JOIN event_types et ON e.event_type_id = et.id
        WHERE et.code = 'trout_shore' OR et.format_code = 'tsf'
    """))
    tsf_event_count = result.scalar()

    if tsf_event_count > 0:
        raise Exception(
            f"Cannot remove TSF: {tsf_event_count} TSF events exist in the database. "
            "Please delete or migrate these events first."
        )

    # =========================================================================
    # Step 2: Drop TSF tables (order matters - child tables first)
    # =========================================================================

    # Drop tsf_leg_positions (depends on tsf_legs)
    op.drop_index('ix_tsf_leg_positions_leg_id', table_name='tsf_leg_positions', if_exists=True)
    op.drop_index('ix_tsf_leg_positions_event_id', table_name='tsf_leg_positions', if_exists=True)
    op.drop_index('ix_tsf_leg_positions_id', table_name='tsf_leg_positions', if_exists=True)
    op.drop_table('tsf_leg_positions', if_exists=True)

    # Drop tsf_day_standings (depends on tsf_days)
    op.drop_index('ix_tsf_day_standings_day_id', table_name='tsf_day_standings', if_exists=True)
    op.drop_index('ix_tsf_day_standings_event_id', table_name='tsf_day_standings', if_exists=True)
    op.drop_index('ix_tsf_day_standings_id', table_name='tsf_day_standings', if_exists=True)
    op.drop_table('tsf_day_standings', if_exists=True)

    # Drop tsf_final_standings (depends on events)
    op.drop_index('ix_tsf_final_standings_final_rank', table_name='tsf_final_standings', if_exists=True)
    op.drop_index('ix_tsf_final_standings_event_id', table_name='tsf_final_standings', if_exists=True)
    op.drop_index('ix_tsf_final_standings_id', table_name='tsf_final_standings', if_exists=True)
    op.drop_table('tsf_final_standings', if_exists=True)

    # Drop tsf_sector_validators (if exists - depends on events)
    op.drop_table('tsf_sector_validators', if_exists=True)

    # Drop tsf_lineups (depends on events)
    op.drop_index('ix_tsf_lineups_club_id', table_name='tsf_lineups', if_exists=True)
    op.drop_index('ix_tsf_lineups_user_id', table_name='tsf_lineups', if_exists=True)
    op.drop_index('ix_tsf_lineups_event_id', table_name='tsf_lineups', if_exists=True)
    op.drop_index('ix_tsf_lineups_id', table_name='tsf_lineups', if_exists=True)
    op.drop_table('tsf_lineups', if_exists=True)

    # Drop tsf_legs (depends on tsf_days)
    op.drop_index('ix_tsf_legs_day_id', table_name='tsf_legs', if_exists=True)
    op.drop_index('ix_tsf_legs_event_id', table_name='tsf_legs', if_exists=True)
    op.drop_index('ix_tsf_legs_id', table_name='tsf_legs', if_exists=True)
    op.drop_table('tsf_legs', if_exists=True)

    # Drop tsf_days (depends on events)
    op.drop_index('ix_tsf_days_event_id', table_name='tsf_days', if_exists=True)
    op.drop_index('ix_tsf_days_id', table_name='tsf_days', if_exists=True)
    op.drop_table('tsf_days', if_exists=True)

    # Drop tsf_event_point_configs (depends on events)
    op.drop_index('ix_tsf_event_point_configs_event_id', table_name='tsf_event_point_configs', if_exists=True)
    op.drop_index('ix_tsf_event_point_configs_id', table_name='tsf_event_point_configs', if_exists=True)
    op.drop_table('tsf_event_point_configs', if_exists=True)

    # Drop tsf_event_settings (depends on events)
    op.drop_index('ix_tsf_event_settings_event_id', table_name='tsf_event_settings', if_exists=True)
    op.drop_index('ix_tsf_event_settings_id', table_name='tsf_event_settings', if_exists=True)
    op.drop_table('tsf_event_settings', if_exists=True)

    # =========================================================================
    # Step 3: Remove TSF columns from user_event_type_stats
    # =========================================================================
    op.drop_column('user_event_type_stats', 'tsf_total_days')
    op.drop_column('user_event_type_stats', 'tsf_sector_wins')
    op.drop_column('user_event_type_stats', 'tsf_total_catches')
    op.drop_column('user_event_type_stats', 'tsf_tournament_wins')
    op.drop_column('user_event_type_stats', 'tsf_tournament_podiums')
    op.drop_column('user_event_type_stats', 'tsf_best_position_points')

    # =========================================================================
    # Step 4: Remove TSF achievements
    # =========================================================================
    # user_achievements uses achievement_id (FK to achievement_definitions)
    op.execute("""
        DELETE FROM user_achievements
        WHERE achievement_id IN (
            SELECT id FROM achievement_definitions
            WHERE code LIKE 'tsf_%'
        )
    """)
    # user_achievement_progress uses achievement_type (VARCHAR matching code)
    op.execute("""
        DELETE FROM user_achievement_progress
        WHERE achievement_type LIKE 'tsf_%'
    """)
    op.execute("DELETE FROM achievement_definitions WHERE code LIKE 'tsf_%'")

    # =========================================================================
    # Step 5: Remove TSF event type (if exists and has no events)
    # =========================================================================
    op.execute("""
        DELETE FROM event_types WHERE code = 'trout_shore' OR format_code = 'tsf'
    """)

    # =========================================================================
    # Step 6: Remove TSF scoring configs
    # =========================================================================
    op.execute("""
        DELETE FROM scoring_configs WHERE format_code = 'tsf' OR code LIKE 'tsf_%'
    """)


def downgrade() -> None:
    """
    Downgrade is not fully supported - TSF removal is intended to be permanent.
    This provides minimal structure recreation without data.
    """
    # Recreate user_event_type_stats columns
    op.add_column('user_event_type_stats',
        sa.Column('tsf_total_days', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('tsf_sector_wins', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('tsf_total_catches', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('tsf_tournament_wins', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('tsf_tournament_podiums', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('tsf_best_position_points', sa.Integer(), nullable=True))

    # Note: Full table recreation would require running the original TSF migrations
    # This downgrade only restores the user_event_type_stats columns
    print("WARNING: TSF tables are not recreated. Run original TSF migrations to restore full functionality.")
