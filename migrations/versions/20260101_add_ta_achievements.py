"""Add TA-specific achievements.

Revision ID: 20260101_add_ta_achievements
Revises: 20260101_add_tsf_stats_to_user_event_type_stats
Create Date: 2026-01-01

Seeds the following TA achievements:
- Match Master (tiered): Bronze/Silver/Gold/Platinum for match wins
- TA Champion (special): Win a Trout Area tournament
- Perfect Leg (special): Win all matches in a single leg
- TA Clean Sheet (special): Win match with opponent at 0 catches

All achievements have applicable_formats = ["ta"]
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260101_add_ta_achievements"
down_revision = "20260101_add_formats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TA-Specific Achievements
    op.execute("""
        INSERT INTO achievement_definitions
        (code, name, description, category, achievement_type, tier, threshold,
         badge_color, applicable_formats, sort_order, is_active, created_at)
        VALUES
        -- TIERED: Match Master (TA match wins)
        ('match_master_bronze', 'Match Rookie', 'Win 10 matches in Trout Area events',
         'tiered', 'ta_match_wins', 'bronze', 10, '#2E7D32', '["ta"]', 600, true, NOW()),
        ('match_master_silver', 'Match Competitor', 'Win 25 matches in Trout Area events',
         'tiered', 'ta_match_wins', 'silver', 25, '#388E3C', '["ta"]', 601, true, NOW()),
        ('match_master_gold', 'Match Expert', 'Win 50 matches in Trout Area events',
         'tiered', 'ta_match_wins', 'gold', 50, '#43A047', '["ta"]', 602, true, NOW()),
        ('match_master_platinum', 'Match Master', 'Win 100 matches in Trout Area events',
         'tiered', 'ta_match_wins', 'platinum', 100, '#66BB6A', '["ta"]', 603, true, NOW()),

        -- SPECIAL: TA Champion
        ('ta_champion', 'TA Champion', 'Win a Trout Area tournament',
         'special', 'ta_tournament_win', NULL, 1, '#FFD700', '["ta"]', 610, true, NOW()),

        -- SPECIAL: Perfect Leg
        ('ta_perfect_leg', 'Perfect Leg', 'Win all matches in a single TA leg',
         'special', 'ta_perfect_leg', NULL, 1, '#2E7D32', '["ta"]', 611, true, NOW()),

        -- SPECIAL: Clean Sheet (TA specific)
        ('ta_clean_sheet', 'TA Clean Sheet', 'Win a match while opponent catches nothing',
         'special', 'ta_clean_sheet', NULL, 1, '#2E7D32', '["ta"]', 612, true, NOW())
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM achievement_definitions
        WHERE code IN (
            'match_master_bronze', 'match_master_silver',
            'match_master_gold', 'match_master_platinum',
            'ta_champion', 'ta_perfect_leg', 'ta_clean_sheet'
        )
    """)
