"""Add species-specific achievements for predator fish.

This migration adds:
- fish_id column to achievement_definitions for species-specific badges
- Tiered achievements for ALL predator species:
  Pike, Zander, Perch, Asp, Volga Zander, Wels Catfish, Chub, Huchen,
  Brown Trout, Rainbow Trout, Brook Trout, Grayling, Ide, Burbot
- Overall predator category achievements

Thresholds:
- Per species: 5/15/30/50 catches (Bronze/Silver/Gold/Platinum)
- Overall predators: 25/75/150/300 catches (Bronze/Silver/Gold/Platinum)

Total new achievements: 14 species × 4 tiers + 4 predator = 60 badges

Revision ID: 20251222_000002
Revises: 20251222_000001
Create Date: 2025-12-22

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251222_000002'
down_revision = '20251222_000001'
branch_labels = None
depends_on = None


# All predator species for achievements (slug, display name, badge name, badge color theme)
PREDATOR_SPECIES = [
    ("pike", "Pike", "Pike Hunter", "#2E7D32"),              # Dark green
    ("zander", "Zander", "Zander Master", "#1565C0"),        # Blue
    ("perch", "Perch", "Perch Expert", "#F57C00"),           # Orange
    ("asp", "Asp", "Asp Slayer", "#6A1B9A"),                 # Purple
    ("volga-zander", "Volga Zander", "Volga Hunter", "#00838F"),  # Teal
    ("wels-catfish", "Wels Catfish", "Catfish King", "#5D4037"),  # Brown
    ("chub", "Chub", "Chub Catcher", "#7B1FA2"),             # Deep purple
    ("huchen", "Huchen", "Huchen Hunter", "#C62828"),        # Red
    ("brown-trout", "Brown Trout", "Trout Master", "#795548"),    # Brown
    ("rainbow-trout", "Rainbow Trout", "Rainbow Chaser", "#E91E63"),  # Pink
    ("brook-trout", "Brook Trout", "Brook Expert", "#009688"),    # Teal
    ("grayling", "Grayling", "Grayling Pro", "#607D8B"),     # Blue grey
    ("ide", "Ide", "Ide Specialist", "#FF5722"),             # Deep orange
    ("burbot", "Burbot", "Burbot Boss", "#3F51B5"),          # Indigo
]

# Tier thresholds for species achievements
SPECIES_TIERS = [
    ("bronze", 5, "#CD7F32"),
    ("silver", 15, "#C0C0C0"),
    ("gold", 30, "#FFD700"),
    ("platinum", 50, "#E5E4E2"),
]

# Overall predator thresholds
PREDATOR_TIERS = [
    ("bronze", 25, "#CD7F32"),
    ("silver", 75, "#C0C0C0"),
    ("gold", 150, "#FFD700"),
    ("platinum", 300, "#E5E4E2"),
]


def upgrade() -> None:
    # Add fish_id column to achievement_definitions
    op.add_column(
        'achievement_definitions',
        sa.Column('fish_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_achievement_definitions_fish_id',
        'achievement_definitions', 'fish',
        ['fish_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_index(
        'ix_achievement_definitions_fish_id',
        'achievement_definitions',
        ['fish_id'],
        unique=False
    )

    # Get connection for lookups
    connection = op.get_bind()

    # Get fish IDs by slug
    fish_ids = {}
    for slug, display_name, badge_name, color in PREDATOR_SPECIES:
        result = connection.execute(
            sa.text("SELECT id FROM fish WHERE slug = :slug"),
            {"slug": slug}
        )
        row = result.fetchone()
        if row:
            fish_ids[slug] = row[0]

    # Build the insert values for species achievements
    values = []
    sort_order = 700  # Start after existing achievements (last was 612)

    # Per-species tiered achievements
    for slug, display_name, badge_name, base_color in PREDATOR_SPECIES:
        if slug not in fish_ids:
            continue
        fish_id = fish_ids[slug]

        for tier, threshold, tier_color in SPECIES_TIERS:
            code = f"fish_{slug.replace('-', '_')}_{tier}"
            if tier == "bronze":
                name = f"{badge_name} Rookie"
                description = f"Catch {threshold} {display_name} fish"
            elif tier == "silver":
                name = f"{badge_name}"
                description = f"Catch {threshold} {display_name} fish"
            elif tier == "gold":
                name = f"{badge_name} Pro"
                description = f"Catch {threshold} {display_name} fish"
            else:  # platinum
                name = f"{badge_name} Legend"
                description = f"Catch {threshold} {display_name} fish"

            values.append(f"""
                ('{code}', '{name}', '{description}', 'tiered', 'fish_catch_count',
                 '{tier}', {threshold}, {fish_id}, '{tier_color}', {sort_order}, true, NOW())
            """)
            sort_order += 1

    # Overall predator category achievements
    for tier, threshold, tier_color in PREDATOR_TIERS:
        code = f"predator_{tier}"
        if tier == "bronze":
            name = "Predator Novice"
            description = f"Catch {threshold} predator fish in total"
        elif tier == "silver":
            name = "Predator Hunter"
            description = f"Catch {threshold} predator fish in total"
        elif tier == "gold":
            name = "Predator Master"
            description = f"Catch {threshold} predator fish in total"
        else:  # platinum
            name = "Apex Predator"
            description = f"Catch {threshold} predator fish in total"

        values.append(f"""
            ('{code}', '{name}', '{description}', 'tiered', 'predator_catch_count',
             '{tier}', {threshold}, NULL, '{tier_color}', {sort_order}, true, NOW())
        """)
        sort_order += 1

    # Insert all species achievements
    if values:
        sql = f"""
            INSERT INTO achievement_definitions
            (code, name, description, category, achievement_type, tier, threshold, fish_id, badge_color, sort_order, is_active, created_at)
            VALUES {','.join(values)}
        """
        op.execute(sql)


def downgrade() -> None:
    # Delete species achievements
    op.execute("""
        DELETE FROM achievement_definitions
        WHERE achievement_type IN ('fish_catch_count', 'predator_catch_count')
    """)

    # Drop fish_id column
    op.drop_index('ix_achievement_definitions_fish_id', 'achievement_definitions')
    op.drop_constraint('fk_achievement_definitions_fish_id', 'achievement_definitions', type_='foreignkey')
    op.drop_column('achievement_definitions', 'fish_id')
