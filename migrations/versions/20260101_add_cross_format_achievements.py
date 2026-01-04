"""Add cross-format achievements.

Revision ID: 20260101_add_xf_achievements
Revises: 20260101_add_tsf_achievements
Create Date: 2026-01-01

Seeds the following cross-format achievements:
- Format Explorer (special): Participate in SF + TA + TSF events
- Triple Threat (special): Win tournaments in all 3 formats
- Versatile Angler (special): Podium finish in all 3 formats

All achievements have applicable_formats = NULL (triggered by any format)
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260101_add_xf_achievements"
down_revision = "20260101_add_tsf_achievements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Cross-Format Achievements
    op.execute("""
        INSERT INTO achievement_definitions
        (code, name, description, category, achievement_type, tier, threshold,
         badge_color, applicable_formats, sort_order, is_active, created_at)
        VALUES
        -- SPECIAL: Format Explorer
        ('format_explorer', 'Format Explorer', 'Participate in Street Fishing, Trout Area, and Trout Shore events',
         'special', 'cross_format_participation', NULL, 1, '#9C27B0', NULL, 800, true, NOW()),

        -- SPECIAL: Triple Threat
        ('triple_threat', 'Triple Threat', 'Win a tournament in all three formats (SF, TA, TSF)',
         'special', 'cross_format_wins', NULL, 1, '#FFD700', NULL, 801, true, NOW()),

        -- SPECIAL: Versatile Angler
        ('versatile_angler', 'Versatile Angler', 'Achieve a podium finish in all three formats',
         'special', 'cross_format_podiums', NULL, 1, '#C0C0C0', NULL, 802, true, NOW())
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM achievement_definitions
        WHERE code IN (
            'format_explorer', 'triple_threat', 'versatile_angler'
        )
    """)
