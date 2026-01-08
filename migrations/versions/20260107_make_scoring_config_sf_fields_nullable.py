"""Make scoring config SF-specific fields nullable

The default_top_x and default_catch_slots fields are only relevant for
Street Fishing (SF) events. TA and TSF use match-based scoring and don't
need these fields. Making them nullable allows proper data modeling.

Revision ID: make_sf_fields_nullable
Revises: enrollment_number_v1
Create Date: 2026-01-07
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'make_sf_fields_nullable'
down_revision = 'enrollment_number_v1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make SF-specific fields nullable
    op.alter_column('scoring_configs', 'default_top_x',
                    existing_type=sa.Integer(),
                    nullable=True)
    op.alter_column('scoring_configs', 'default_catch_slots',
                    existing_type=sa.Integer(),
                    nullable=True)

    # Clear SF-specific values for TA/TSF scoring configs
    op.execute("""
        UPDATE scoring_configs
        SET default_top_x = NULL, default_catch_slots = NULL
        WHERE code LIKE 'ta_%' OR code LIKE 'tsf_%'
    """)


def downgrade() -> None:
    # Restore default values for TA/TSF configs
    op.execute("""
        UPDATE scoring_configs
        SET default_top_x = 10, default_catch_slots = 5
        WHERE code LIKE 'ta_%' OR code LIKE 'tsf_%'
    """)

    # Make columns NOT NULL again
    op.alter_column('scoring_configs', 'default_top_x',
                    existing_type=sa.Integer(),
                    nullable=False,
                    server_default='10')
    op.alter_column('scoring_configs', 'default_catch_slots',
                    existing_type=sa.Integer(),
                    nullable=False,
                    server_default='5')
