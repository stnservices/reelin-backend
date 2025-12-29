"""Add fish slug field and seed European fish species.

Revision ID: 20251220_000004
Revises: 20251220_000003
Create Date: 2025-12-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20251220_000004"
down_revision: Union[str, None] = "20251220_000003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Fish species data: (slug, name, scientific_name, min_length, max_length)
FISH_SPECIES = [
    # PREDATORS
    ("pike", "Pike", "Esox lucius", 50.0, 150.0),
    ("zander", "Zander", "Sander lucioperca", 40.0, 130.0),
    ("perch", "European Perch", "Perca fluviatilis", 15.0, 60.0),
    ("wels-catfish", "Wels Catfish", "Silurus glanis", 100.0, 500.0),
    ("asp", "Asp", "Leuciscus aspius", 30.0, 120.0),
    ("chub", "Chub", "Squalius cephalus", 25.0, 80.0),
    ("huchen", "Huchen", "Hucho hucho", 60.0, 150.0),
    ("brown-trout", "Brown Trout", "Salmo trutta", 25.0, 140.0),
    ("rainbow-trout", "Rainbow Trout", "Oncorhynchus mykiss", 20.0, 120.0),
    ("brook-trout", "Brook Trout", "Salvelinus fontinalis", 15.0, 86.0),
    ("grayling", "Grayling", "Thymallus thymallus", 25.0, 60.0),
    ("volga-zander", "Volga Zander", "Sander volgensis", 30.0, 45.0),
    ("ide", "Ide", "Leuciscus idus", 25.0, 85.0),
    ("burbot", "Burbot", "Lota lota", 40.0, 152.0),
    ("ruffe", "Ruffe", "Gymnocephalus cernua", 10.0, 25.0),
    # NON-PREDATORS
    ("carp", "Common Carp", "Cyprinus carpio", 35.0, 120.0),
    ("crucian-carp", "Crucian Carp", "Carassius carassius", 15.0, 64.0),
    ("grass-carp", "Grass Carp", "Ctenopharyngodon idella", 60.0, 150.0),
    ("silver-carp", "Silver Carp", "Hypophthalmichthys molitrix", 60.0, 140.0),
    ("bighead-carp", "Bighead Carp", "Hypophthalmichthys nobilis", 60.0, 146.0),
    ("bream", "Common Bream", "Abramis brama", 30.0, 90.0),
    ("white-bream", "White Bream", "Blicca bjoerkna", 20.0, 45.0),
    ("roach", "Roach", "Rutilus rutilus", 15.0, 53.0),
    ("rudd", "Rudd", "Scardinius erythrophthalmus", 20.0, 51.0),
    ("tench", "Tench", "Tinca tinca", 25.0, 84.0),
    ("barbel", "Barbel", "Barbus barbus", 30.0, 120.0),
    ("nase", "Nase", "Chondrostoma nasus", 25.0, 60.0),
    ("bleak", "Bleak", "Alburnus alburnus", 10.0, 25.0),
]


def upgrade() -> None:
    # Add slug column as nullable first
    op.add_column("fish", sa.Column("slug", sa.String(100), nullable=True))

    # Update existing fish with slugs derived from name
    connection = op.get_bind()

    # Get existing fish
    result = connection.execute(sa.text("SELECT id, name FROM fish"))
    existing_fish = result.fetchall()

    # Update existing fish with slugs
    for fish_id, name in existing_fish:
        slug = name.lower().replace(" ", "-").replace("(", "").replace(")", "")
        connection.execute(
            sa.text("UPDATE fish SET slug = :slug WHERE id = :id"),
            {"slug": slug, "id": fish_id}
        )

    # Get existing slugs to avoid duplicates
    result = connection.execute(sa.text("SELECT slug FROM fish WHERE slug IS NOT NULL"))
    existing_slugs = {row[0] for row in result.fetchall()}

    # Insert new fish species (only if slug doesn't exist)
    for slug, name, scientific_name, min_length, max_length in FISH_SPECIES:
        if slug not in existing_slugs:
            connection.execute(
                sa.text("""
                    INSERT INTO fish (slug, name, scientific_name, min_length, max_length, is_active, created_at)
                    VALUES (:slug, :name, :scientific_name, :min_length, :max_length, true, NOW())
                """),
                {
                    "slug": slug,
                    "name": name,
                    "scientific_name": scientific_name,
                    "min_length": min_length,
                    "max_length": max_length,
                }
            )
            existing_slugs.add(slug)

    # Make slug not nullable and add unique constraint
    op.alter_column("fish", "slug", nullable=False)
    op.create_unique_constraint("uq_fish_slug", "fish", ["slug"])
    op.create_index("ix_fish_slug", "fish", ["slug"])


def downgrade() -> None:
    op.drop_index("ix_fish_slug", table_name="fish")
    op.drop_constraint("uq_fish_slug", "fish", type_="unique")
    op.drop_column("fish", "slug")
