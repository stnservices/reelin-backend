"""Populate fish name translations (Romanian)

Revision ID: fish_translations_001
Revises: chat_messages_001
Create Date: 2026-01-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fish_translations_001'
down_revision: Union[str, None] = 'chat_messages_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Fish translations: (slug, name_en, name_ro)
FISH_TRANSLATIONS = [
    # PREDATORS - Main Species
    ("pike", "Pike", "Știucă"),
    ("zander", "Zander", "Șalău"),
    ("perch", "European Perch", "Biban"),
    ("wels-catfish", "Wels Catfish", "Somn"),
    ("asp", "Asp", "Avat"),
    ("chub", "Chub", "Clean"),
    ("huchen", "Huchen", "Lostriță"),
    ("burbot", "Burbot", "Mihalț"),
    ("ruffe", "Ruffe", "Ghiborț"),
    ("ide", "Ide", "Văduviță"),
    ("volga-zander", "Volga Zander", "Șalău Vărgat"),

    # PREDATORS - Trout & Salmonids
    ("trout", "Trout", "Păstrăv"),
    ("brown-trout", "Brown Trout", "Păstrăv Indigen"),
    ("rainbow-trout", "Rainbow Trout", "Păstrăv Curcubeu"),
    ("brook-trout", "Brook Trout", "Păstrăv Fântânel"),
    ("lake-trout", "Lake Trout", "Păstrăv de Lac"),
    ("sea-trout", "Sea Trout", "Păstrăv de Mare"),
    ("grayling", "Grayling", "Lipan"),
    ("atlantic-salmon", "Atlantic Salmon", "Somon"),
    ("arctic-char", "Arctic Char", "Char Arctic"),

    # PREDATORS - Additional European Species
    ("european-eel", "European Eel", "Țipar"),
    ("european-bass", "European Bass", "Biban de Mare"),
    ("black-bass", "Black Bass", "Biban Negru"),
    ("largemouth-bass", "Largemouth Bass", "Biban cu Gură Mare"),
    ("smallmouth-bass", "Smallmouth Bass", "Biban cu Gură Mică"),

    # NON-PREDATORS (in case they are added later)
    ("carp", "Common Carp", "Crap"),
    ("crucian-carp", "Crucian Carp", "Caras"),
    ("grass-carp", "Grass Carp", "Crap Iarbă"),
    ("silver-carp", "Silver Carp", "Novac"),
    ("bighead-carp", "Bighead Carp", "Sânger"),
    ("bream", "Common Bream", "Plătică"),
    ("white-bream", "White Bream", "Cosac"),
    ("roach", "Roach", "Babușcă"),
    ("rudd", "Rudd", "Roșioară"),
    ("tench", "Tench", "Lin"),
    ("barbel", "Barbel", "Mreană"),
    ("nase", "Nase", "Scobar"),
    ("bleak", "Bleak", "Oblețul"),
]


def upgrade() -> None:
    connection = op.get_bind()

    for slug, name_en, name_ro in FISH_TRANSLATIONS:
        connection.execute(
            sa.text("""
                UPDATE fish
                SET name_en = :name_en, name_ro = :name_ro
                WHERE slug = :slug
            """),
            {"slug": slug, "name_en": name_en, "name_ro": name_ro}
        )


def downgrade() -> None:
    connection = op.get_bind()

    for slug, _, _ in FISH_TRANSLATIONS:
        connection.execute(
            sa.text("""
                UPDATE fish
                SET name_en = NULL, name_ro = NULL
                WHERE slug = :slug
            """),
            {"slug": slug}
        )
