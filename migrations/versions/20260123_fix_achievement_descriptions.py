"""Fix achievement descriptions after TSF removal.

Updates descriptions for cross-format achievements to reflect
that we only have SF and TA formats now (TSF was removed).

Also ensures triple_threat and dual_champion are aligned.
"""

from alembic import op

revision = '20260123_fix_descriptions'
down_revision = None  # Will be set by alembic
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Update Format Explorer description
    op.execute("""
        UPDATE achievement_definitions
        SET description = 'Participate in both Street Fishing and Trout Area events'
        WHERE code = 'format_explorer'
    """)

    # Update Triple Threat description (now Dual Champion concept - win in both formats)
    op.execute("""
        UPDATE achievement_definitions
        SET description = 'Win a tournament in both Street Fishing and Trout Area formats'
        WHERE code = 'triple_threat'
    """)

    # Update Versatile Angler description
    op.execute("""
        UPDATE achievement_definitions
        SET description = 'Achieve a podium finish in both Street Fishing and Trout Area formats'
        WHERE code = 'versatile_angler'
    """)

    # If triple_threat exists but dual_champion is what code uses, rename triple_threat to dual_champion
    # Or if dual_champion doesn't exist, create an alias
    op.execute("""
        UPDATE achievement_definitions
        SET code = 'dual_champion',
            name = 'Dual Champion'
        WHERE code = 'triple_threat'
        AND NOT EXISTS (SELECT 1 FROM achievement_definitions WHERE code = 'dual_champion')
    """)


def downgrade() -> None:
    # Revert to original descriptions
    op.execute("""
        UPDATE achievement_definitions
        SET description = 'Participate in Street Fishing, Trout Area, and Trout Shore events'
        WHERE code = 'format_explorer'
    """)

    op.execute("""
        UPDATE achievement_definitions
        SET description = 'Win a tournament in all three formats (SF, TA, TSF)'
        WHERE code IN ('triple_threat', 'dual_champion')
    """)

    op.execute("""
        UPDATE achievement_definitions
        SET description = 'Achieve a podium finish in all three formats'
        WHERE code = 'versatile_angler'
    """)
