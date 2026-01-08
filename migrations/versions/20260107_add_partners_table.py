"""Add partners table for landing page.

Revision ID: add_partners_table
Revises: make_sf_fields_nullable
Create Date: 2026-01-07

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_partners_table'
down_revision = 'make_sf_fields_nullable'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'partners',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('logo_url', sa.String(500), nullable=False),
        sa.Column('website_url', sa.String(500), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_partners_active_order', 'partners', ['is_active', 'display_order'])


def downgrade() -> None:
    op.drop_index('idx_partners_active_order', table_name='partners')
    op.drop_table('partners')
