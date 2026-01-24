"""Remove TSF sector dominator achievements.

These achievements reference tsf_sector_wins which no longer exists
since TSF format was eliminated.
"""

from typing import Union
from alembic import op

revision: str = 'rm_tsf_sector_001'
down_revision: Union[str, None] = 'sf_team_ach_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Delete user achievements for sector_dominator badges
    op.execute("""
        DELETE FROM user_achievements
        WHERE achievement_id IN (
            SELECT id FROM achievement_definitions
            WHERE code LIKE 'sector_dominator_%'
               OR achievement_type = 'tsf_sector_wins'
        )
    """)

    # Delete progress tracking
    op.execute("""
        DELETE FROM user_achievement_progress
        WHERE achievement_type = 'tsf_sector_wins'
    """)

    # Delete the achievement definitions
    op.execute("""
        DELETE FROM achievement_definitions
        WHERE code LIKE 'sector_dominator_%'
           OR achievement_type = 'tsf_sector_wins'
    """)


def downgrade() -> None:
    # Re-insert sector dominator achievements (if needed)
    op.execute("""
        INSERT INTO achievement_definitions (code, name, description, category, achievement_type, tier, threshold, sort_order)
        VALUES
            ('sector_dominator_bronze', 'Sector Rookie', 'Win 5 sector legs in TSF events', 'tiered', 'tsf_sector_wins', 'bronze', 5, 0),
            ('sector_dominator_silver', 'Sector Competitor', 'Win 15 sector legs in TSF events', 'tiered', 'tsf_sector_wins', 'silver', 15, 0),
            ('sector_dominator_gold', 'Sector Expert', 'Win 30 sector legs in TSF events', 'tiered', 'tsf_sector_wins', 'gold', 30, 0),
            ('sector_dominator_platinum', 'Sector Dominator', 'Win 50 sector legs in TSF events', 'tiered', 'tsf_sector_wins', 'platinum', 50, 0)
        ON CONFLICT (code) DO NOTHING
    """)
