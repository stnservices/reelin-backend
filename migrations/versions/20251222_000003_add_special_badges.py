"""Add special one-time achievement badges.

This migration seeds special badges that are earned for unique accomplishments:
- First Blood: First ever validated catch
- Early Bird: First approved catch within 30 min of event start
- Last Minute Hero: Approved catch in final 30 minutes
- Speed Demon: 5 approved catches in first hour
- Trophy Hunter: Catch a fish >= 50cm
- Monster Catch: Set a new personal best length
- Precision Angler: 90%+ catches above min length in single event
- Hot Streak: 3 podium finishes in a row
- Dominator: 2 wins in a row
- Iron Man: 5 consecutive events participated
- Clean Sheet: Event with no rejected catches (min 3 catches)
- Comeback King: Improve 5+ ranks from initial position
- Diversity Master: Catch every available species in single event

Revision ID: 20251222_000003
Revises: 20251222_000002
Create Date: 2025-12-22

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251222_000003'
down_revision = '20251222_000002'
branch_labels = None
depends_on = None


# Special badges: (code, name, description, badge_color)
SPECIAL_BADGES = [
    ("first_blood", "First Blood", "Get your first ever validated catch", "#C62828"),
    ("early_bird", "Early Bird", "Submit first approved catch within 30 minutes of event start", "#FF8F00"),
    ("last_minute", "Last Minute Hero", "Submit an approved catch in the final 30 minutes", "#5E35B1"),
    ("speed_demon", "Speed Demon", "Get 5 approved catches in the first hour of an event", "#D84315"),
    ("trophy_hunter", "Trophy Hunter", "Catch a fish 50cm or larger", "#FFD700"),
    ("monster_catch", "Monster Catch", "Set a new personal best catch length", "#2E7D32"),
    ("precision_angler", "Precision Angler", "Achieve 90%+ approval rate in a single event (min 5 catches)", "#1565C0"),
    ("hot_streak", "Hot Streak", "Finish on the podium 3 events in a row", "#F57C00"),
    ("dominator", "Dominator", "Win 2 events in a row", "#6A1B9A"),
    ("iron_man", "Iron Man", "Participate in 5 consecutive events", "#455A64"),
    ("clean_sheet", "Clean Sheet", "Complete an event with no rejected catches (min 3 catches)", "#00897B"),
    ("comeback_king", "Comeback King", "Improve your rank by 5 or more positions during an event", "#00ACC1"),
    ("diversity_master", "Diversity Master", "Catch every available species in a single event", "#8E24AA"),
]


def upgrade() -> None:
    # Build the insert values for special achievements
    values = []
    sort_order = 800  # Start after species achievements

    for code, name, description, badge_color in SPECIAL_BADGES:
        values.append(f"""
            ('{code}', '{name}', '{description}', 'special', '{code}',
             NULL, NULL, NULL, '{badge_color}', {sort_order}, true, NOW())
        """)
        sort_order += 1

    # Insert all special achievements
    if values:
        sql = f"""
            INSERT INTO achievement_definitions
            (code, name, description, category, achievement_type, tier, threshold, fish_id, badge_color, sort_order, is_active, created_at)
            VALUES {','.join(values)}
        """
        op.execute(sql)


def downgrade() -> None:
    # Delete special achievements
    codes = [f"'{code}'" for code, _, _, _ in SPECIAL_BADGES]
    op.execute(f"""
        DELETE FROM achievement_definitions
        WHERE code IN ({','.join(codes)})
    """)
