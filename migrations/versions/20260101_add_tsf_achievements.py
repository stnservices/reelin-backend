"""Add TSF-specific achievements.

Revision ID: 20260101_add_tsf_achievements
Revises: 20260101_add_ta_achievements
Create Date: 2026-01-01

Seeds the following TSF achievements:
- Sector Dominator (tiered): Bronze/Silver/Gold/Platinum for sector wins
- TSF Champion (special): Win a Trout Shore tournament
- Day Winner (special): Finish 1st place in a TSF competition day
- Consistent Performer (special): Finish top 5 every day in a multi-day TSF event

All achievements have applicable_formats = ["tsf"]
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260101_add_tsf_achievements"
down_revision = "20260101_add_ta_achievements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TSF-Specific Achievements
    op.execute("""
        INSERT INTO achievement_definitions
        (code, name, description, category, achievement_type, tier, threshold,
         badge_color, applicable_formats, sort_order, is_active, created_at)
        VALUES
        -- TIERED: Sector Dominator (TSF sector wins)
        ('sector_dominator_bronze', 'Sector Rookie', 'Win 5 sector legs in TSF events',
         'tiered', 'tsf_sector_wins', 'bronze', 5, '#FF8F00', '["tsf"]', 700, true, NOW()),
        ('sector_dominator_silver', 'Sector Competitor', 'Win 15 sector legs in TSF events',
         'tiered', 'tsf_sector_wins', 'silver', 15, '#FFA000', '["tsf"]', 701, true, NOW()),
        ('sector_dominator_gold', 'Sector Expert', 'Win 30 sector legs in TSF events',
         'tiered', 'tsf_sector_wins', 'gold', 30, '#FFB300', '["tsf"]', 702, true, NOW()),
        ('sector_dominator_platinum', 'Sector Dominator', 'Win 50 sector legs in TSF events',
         'tiered', 'tsf_sector_wins', 'platinum', 50, '#FFC107', '["tsf"]', 703, true, NOW()),

        -- SPECIAL: TSF Champion
        ('tsf_champion', 'TSF Champion', 'Win a Trout Shore Fishing tournament',
         'special', 'tsf_tournament_win', NULL, 1, '#FFD700', '["tsf"]', 710, true, NOW()),

        -- SPECIAL: Day Winner
        ('tsf_day_winner', 'Day Winner', 'Finish 1st place in a TSF competition day',
         'special', 'tsf_day_winner', NULL, 1, '#FF8F00', '["tsf"]', 711, true, NOW()),

        -- SPECIAL: Consistent Performer
        ('tsf_consistent_performer', 'Consistent Performer', 'Finish top 5 every day in a multi-day TSF event',
         'special', 'tsf_consistent_performer', NULL, 1, '#FF8F00', '["tsf"]', 712, true, NOW())
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM achievement_definitions
        WHERE code IN (
            'sector_dominator_bronze', 'sector_dominator_silver',
            'sector_dominator_gold', 'sector_dominator_platinum',
            'tsf_champion', 'tsf_day_winner', 'tsf_consistent_performer'
        )
    """)
