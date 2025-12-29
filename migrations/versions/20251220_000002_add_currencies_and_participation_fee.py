"""Add currencies table and participation fee to events.

This migration adds:
- currencies table for storing currency information (code, name, symbol)
- participation_fee and participation_fee_currency_id to events table
- Seed data for common currencies (RON, EUR, USD, GBP, HUF)

This enables:
- Organizers to specify participation fees for events
- Anglers to see the participation cost upfront

Revision ID: 20251220_000002
Revises: 20251220_000001
Create Date: 2025-12-20

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251220_000002'
down_revision = '20251220_000001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create currencies table
    op.create_table(
        'currencies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('code', sa.String(3), nullable=False),
        sa.Column('symbol', sa.String(10), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code')
    )
    op.create_index('ix_currencies_id', 'currencies', ['id'], unique=False)

    # Add participation fee columns to events
    op.add_column(
        'events',
        sa.Column('participation_fee', sa.Numeric(10, 2), nullable=True)
    )
    op.add_column(
        'events',
        sa.Column('participation_fee_currency_id', sa.Integer(), nullable=True)
    )

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_events_participation_fee_currency_id',
        'events',
        'currencies',
        ['participation_fee_currency_id'],
        ['id'],
        ondelete='SET NULL'
    )
    op.create_index(
        'ix_events_participation_fee_currency_id',
        'events',
        ['participation_fee_currency_id'],
        unique=False
    )

    # Seed common currencies
    currencies_table = sa.table(
        'currencies',
        sa.column('name', sa.String),
        sa.column('code', sa.String),
        sa.column('symbol', sa.String),
        sa.column('is_active', sa.Boolean),
    )
    op.bulk_insert(
        currencies_table,
        [
            {'name': 'Romanian Leu', 'code': 'RON', 'symbol': 'lei', 'is_active': True},
            {'name': 'Euro', 'code': 'EUR', 'symbol': 'EUR', 'is_active': True},
            {'name': 'US Dollar', 'code': 'USD', 'symbol': '$', 'is_active': True},
            {'name': 'British Pound', 'code': 'GBP', 'symbol': '£', 'is_active': True},
            {'name': 'Hungarian Forint', 'code': 'HUF', 'symbol': 'Ft', 'is_active': True},
        ]
    )


def downgrade() -> None:
    # Drop index and foreign key
    op.drop_index('ix_events_participation_fee_currency_id', 'events')
    op.drop_constraint('fk_events_participation_fee_currency_id', 'events', type_='foreignkey')

    # Drop participation fee columns from events
    op.drop_column('events', 'participation_fee_currency_id')
    op.drop_column('events', 'participation_fee')

    # Drop currencies table
    op.drop_index('ix_currencies_id', 'currencies')
    op.drop_table('currencies')
