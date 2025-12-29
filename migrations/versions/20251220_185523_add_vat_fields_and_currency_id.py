"""add_vat_fields_and_currency_id

Revision ID: c7b94352e568
Revises: 20251220_000004
Create Date: 2025-12-20 18:55:23.452467+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7b94352e568'
down_revision: Union[str, None] = '20251220_000004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add VAT fields to organizer_billing_profiles
    op.add_column('organizer_billing_profiles', sa.Column('is_vat_payer', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('organizer_billing_profiles', sa.Column('vat_rate', sa.Numeric(precision=5, scale=2), nullable=True))

    # Add currency_id to pricing_tiers (nullable first for existing data)
    op.add_column('pricing_tiers', sa.Column('currency_id', sa.Integer(), nullable=True))

    # Update existing rows to use the first available currency (if any exist)
    op.execute("""
        UPDATE pricing_tiers
        SET currency_id = (SELECT id FROM currencies WHERE is_active = true LIMIT 1)
        WHERE currency_id IS NULL
    """)

    # Now make it not nullable and add foreign key
    op.alter_column('pricing_tiers', 'currency_id', nullable=False)
    op.create_index(op.f('ix_pricing_tiers_currency_id'), 'pricing_tiers', ['currency_id'], unique=False)
    op.create_foreign_key('fk_pricing_tiers_currency_id', 'pricing_tiers', 'currencies', ['currency_id'], ['id'], ondelete='RESTRICT')

    # Drop old currency_code column
    op.drop_column('pricing_tiers', 'currency_code')


def downgrade() -> None:
    # Add back currency_code column
    op.add_column('pricing_tiers', sa.Column('currency_code', sa.VARCHAR(length=3), nullable=True))

    # Restore currency_code from the currency relationship
    op.execute("""
        UPDATE pricing_tiers pt
        SET currency_code = c.code
        FROM currencies c
        WHERE pt.currency_id = c.id
    """)

    op.alter_column('pricing_tiers', 'currency_code', nullable=False, server_default='EUR')

    # Drop currency_id
    op.drop_constraint('fk_pricing_tiers_currency_id', 'pricing_tiers', type_='foreignkey')
    op.drop_index(op.f('ix_pricing_tiers_currency_id'), table_name='pricing_tiers')
    op.drop_column('pricing_tiers', 'currency_id')

    # Drop VAT fields
    op.drop_column('organizer_billing_profiles', 'vat_rate')
    op.drop_column('organizer_billing_profiles', 'is_vat_payer')
