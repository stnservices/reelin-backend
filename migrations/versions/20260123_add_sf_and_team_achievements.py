"""Add SF Champion and Team achievements.

Adds:
- SF Champion: Win a National Street Fishing Championship
- Team Player: Participate in your first team event
- Team Champion: Win a team event
- Team Spirit (Bronze/Silver/Gold): Win multiple team events
"""

from typing import Union
from alembic import op

revision: str = 'sf_team_ach_001'
down_revision: Union[str, None] = 'fix_ach_desc_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Update TA Champion description (already exists, needs description update)
    op.execute("""
        UPDATE achievement_definitions
        SET description = 'Win a National Trout Area Championship'
        WHERE code = 'ta_champion'
    """)

    # SF Champion - Hall of Fame SF tournament winner
    op.execute("""
        INSERT INTO achievement_definitions (
            code, name, description, category, achievement_type, tier,
            applicable_formats, sort_order, is_active
        )
        VALUES (
            'sf_champion', 'SF Champion', 'Win a National Street Fishing Championship',
            'special', 'one_time', NULL,
            '["sf"]', 100, true
        )
        ON CONFLICT (code) DO NOTHING
    """)

    # Team Player - first team event participation
    op.execute("""
        INSERT INTO achievement_definitions (
            code, name, description, category, achievement_type, tier,
            sort_order, is_active
        )
        VALUES (
            'team_player', 'Team Player', 'Participate in your first team event',
            'special', 'one_time', NULL,
            101, true
        )
        ON CONFLICT (code) DO NOTHING
    """)

    # Team Champion - win a team event
    op.execute("""
        INSERT INTO achievement_definitions (
            code, name, description, category, achievement_type, tier,
            sort_order, is_active
        )
        VALUES (
            'team_champion', 'Team Champion', 'Win a team event with your team',
            'special', 'one_time', NULL,
            102, true
        )
        ON CONFLICT (code) DO NOTHING
    """)

    # Team Spirit Bronze - win 3 team events
    op.execute("""
        INSERT INTO achievement_definitions (
            code, name, description, category, achievement_type, tier,
            threshold, sort_order, is_active
        )
        VALUES (
            'team_spirit_bronze', 'Team Spirit', 'Win 3 team events',
            'tiered', 'team_wins', 'bronze',
            3, 103, true
        )
        ON CONFLICT (code) DO NOTHING
    """)

    # Team Spirit Silver - win 5 team events
    op.execute("""
        INSERT INTO achievement_definitions (
            code, name, description, category, achievement_type, tier,
            threshold, sort_order, is_active
        )
        VALUES (
            'team_spirit_silver', 'Team Spirit', 'Win 5 team events',
            'tiered', 'team_wins', 'silver',
            5, 104, true
        )
        ON CONFLICT (code) DO NOTHING
    """)

    # Team Spirit Gold - win 10 team events
    op.execute("""
        INSERT INTO achievement_definitions (
            code, name, description, category, achievement_type, tier,
            threshold, sort_order, is_active
        )
        VALUES (
            'team_spirit_gold', 'Team Spirit', 'Win 10 team events',
            'tiered', 'team_wins', 'gold',
            10, 105, true
        )
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade() -> None:
    # Revert TA Champion description
    op.execute("""
        UPDATE achievement_definitions
        SET description = 'Win a Trout Area tournament'
        WHERE code = 'ta_champion'
    """)

    op.execute("""
        DELETE FROM achievement_definitions
        WHERE code IN (
            'sf_champion', 'team_player', 'team_champion',
            'team_spirit_bronze', 'team_spirit_silver', 'team_spirit_gold'
        )
    """)
