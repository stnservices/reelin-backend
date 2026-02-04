"""Fix fish slugs for species badge system.

This migration fixes the fish table data to enable species badges:
1. Updates achievement_definitions.fish_id to point to active fish records
2. Deletes unused old fish records (English slugs with zero catches)
3. Updates Romanian fish slugs to English equivalents

Background:
- The fish table had duplicate entries: OLD (inactive, English slugs) and NEW (active, Romanian slugs)
- PREDATOR_FISH_SLUGS in achievement_service.py uses English slugs: ["pike", "zander", "perch", ...]
- All catches used Romanian fish IDs (biban, stiuca, salau, etc.)
- Species badges were impossible to earn because fish_slug never matched PREDATOR_FISH_SLUGS

Fish ID mappings:
| OLD ID | OLD Slug       | NEW ID | NEW Slug (before) | NEW Slug (after) |
|--------|----------------|--------|-------------------|------------------|
| 3      | perch          | 33     | biban             | perch            |
| 2      | pike           | 35     | stiuca            | pike             |
| 5      | zander         | 31     | salau             | zander           |
| 8      | chub           | 34     | clean             | chub             |
| 9      | asp            | 37     | avat              | asp              |
| 16     | wels-catfish   | 36     | somn              | wels-catfish     |
| 18     | brown-trout    | 32     | pastrav           | brown-trout      |
| 21     | volga-zander   | 39     | salau-vargat      | volga-zander     |
| 22     | ide            | 38     | vaduvita          | ide              |

NOTE: This migration was applied manually to production on 2026-02-04.
The upgrade() function is idempotent and safe to run on existing data.
"""

from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = 'fix_fish_slugs_001'
down_revision: Union[str, None] = 'ads_enabled_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Fix fish slugs and achievement_definitions references.

    This migration is idempotent - safe to run multiple times.
    All operations check current state before making changes.
    """
    connection = op.get_bind()

    # Define the fish ID mappings (OLD unused ID -> NEW active ID)
    achievement_fish_mappings = [
        (3, 33),   # perch badges -> biban (now perch)
        (2, 35),   # pike badges -> stiuca (now pike)
        (5, 31),   # zander badges -> salau (now zander)
        (8, 34),   # chub badges -> clean (now chub)
        (9, 37),   # asp badges -> avat (now asp)
        (16, 36),  # wels-catfish badges -> somn (now wels-catfish)
        (18, 32),  # brown-trout badges -> pastrav (now brown-trout)
        (21, 39),  # volga-zander badges -> salau-vargat (now volga-zander)
        (22, 38),  # ide badges -> vaduvita (now ide)
    ]

    # Fish IDs that have no active equivalent (set fish_id to NULL)
    fish_with_no_active = [17, 19, 20, 23]  # huchen, brook-trout, grayling, burbot

    # Old fish IDs to delete (after updating references)
    old_fish_to_delete = [2, 3, 5, 8, 9, 16, 17, 18, 19, 20, 21, 22, 23]

    # Romanian slug -> English slug mappings
    slug_updates = [
        (33, 'perch'),        # biban -> perch
        (35, 'pike'),         # stiuca -> pike
        (31, 'zander'),       # salau -> zander
        (34, 'chub'),         # clean -> chub
        (37, 'asp'),          # avat -> asp
        (36, 'wels-catfish'), # somn -> wels-catfish
        (32, 'brown-trout'),  # pastrav -> brown-trout
        (39, 'volga-zander'), # salau-vargat -> volga-zander
        (38, 'ide'),          # vaduvita -> ide
    ]

    # Step 1: Update achievement_definitions to point to active fish IDs
    for old_id, new_id in achievement_fish_mappings:
        connection.execute(
            sa.text("""
                UPDATE achievement_definitions
                SET fish_id = :new_id
                WHERE fish_id = :old_id
            """),
            {'old_id': old_id, 'new_id': new_id}
        )

    # Step 2: Set fish_id to NULL for fish with no active equivalent
    for fish_id in fish_with_no_active:
        connection.execute(
            sa.text("""
                UPDATE achievement_definitions
                SET fish_id = NULL
                WHERE fish_id = :fish_id
            """),
            {'fish_id': fish_id}
        )

    # Step 3: Delete old unused fish records (now safe - no references)
    connection.execute(
        sa.text("""
            DELETE FROM fish
            WHERE id = ANY(:ids)
            AND NOT EXISTS (SELECT 1 FROM catches WHERE fish_id = fish.id)
            AND NOT EXISTS (SELECT 1 FROM achievement_definitions WHERE fish_id = fish.id)
        """),
        {'ids': old_fish_to_delete}
    )

    # Step 4: Update Romanian slugs to English (now safe - no duplicates)
    for fish_id, english_slug in slug_updates:
        # Only update if current slug is different (idempotent)
        connection.execute(
            sa.text("""
                UPDATE fish
                SET slug = :slug
                WHERE id = :id AND slug != :slug
            """),
            {'id': fish_id, 'slug': english_slug}
        )


def downgrade() -> None:
    """Revert fish slug changes.

    WARNING: This will break species badges again.
    Only use if absolutely necessary.
    """
    connection = op.get_bind()

    # Revert English slugs back to Romanian
    slug_reverts = [
        (33, 'biban'),        # perch -> biban
        (35, 'stiuca'),       # pike -> stiuca
        (31, 'salau'),        # zander -> salau
        (34, 'clean'),        # chub -> clean
        (37, 'avat'),         # asp -> avat
        (36, 'somn'),         # wels-catfish -> somn
        (32, 'pastrav'),      # brown-trout -> pastrav
        (39, 'salau-vargat'), # volga-zander -> salau-vargat
        (38, 'vaduvita'),     # ide -> vaduvita
    ]

    for fish_id, romanian_slug in slug_reverts:
        connection.execute(
            sa.text("""
                UPDATE fish
                SET slug = :slug
                WHERE id = :id
            """),
            {'id': fish_id, 'slug': romanian_slug}
        )

    # Note: We don't recreate the deleted old fish records in downgrade
    # as that would require knowing all their original attributes.
    # The achievement_definitions.fish_id updates are also not reverted
    # as the old fish records no longer exist.
