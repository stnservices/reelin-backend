"""Add format_code to event_types table.

format_code determines which competition format/wizard to use:
- 'sf' = Street Fishing format (catch & measure, individual scoring)
- 'ta' = Trout Area format (head-to-head matches, game cards)
- 'tsf' = Trout Shore Fishing format (multi-day, sectors, validators)

Revision ID: 20251229_151700
Revises: 20251228_240001
Create Date: 2025-12-29 15:17:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20251229_151700'
down_revision: Union[str, None] = '20251229_200001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add format_code column with default 'sf'
    op.add_column('event_types', sa.Column('format_code', sa.String(10), nullable=False, server_default='sf'))

    # Update existing event types with correct format_code
    op.execute("""
        UPDATE event_types SET format_code = 'sf' WHERE code = 'street_fishing';
        UPDATE event_types SET format_code = 'ta' WHERE code = 'trout_area';
        UPDATE event_types SET format_code = 'tsf' WHERE code = 'trout_shore';
    """)

    # Add new event types that use SF format
    op.execute("""
        INSERT INTO event_types (name, code, format_code, description, is_active)
        VALUES
            ('Boat Fishing', 'boat_fishing', 'sf', 'Boat-based fishing competitions using street fishing format', true),
            ('Predator Cup', 'predator_cup', 'sf', 'Predator fishing tournaments - pike, zander, perch', true),
            ('Aquachallenge', 'aquachallenge', 'sf', 'Multi-species fishing challenge events', true)
        ON CONFLICT (code) DO NOTHING;
    """)

    # Link new event types to SF scoring configs
    op.execute("""
        INSERT INTO scoring_config_event_types (scoring_config_id, event_type_id)
        SELECT sc.id, et.id
        FROM scoring_configs sc
        CROSS JOIN event_types et
        WHERE sc.code IN ('sf_top_x_overall', 'sf_top_x_by_species')
          AND et.code IN ('boat_fishing', 'predator_cup', 'aquachallenge')
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    # Remove new event types
    op.execute("""
        DELETE FROM event_types WHERE code IN ('boat_fishing', 'predator_cup', 'aquachallenge');
    """)

    # Remove format_code column
    op.drop_column('event_types', 'format_code')
