"""Add tier and contact_email fields to sponsors table.

Revision ID: 20251216_000002
Revises: 20251216_000001
Create Date: 2025-12-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20251216_000002'
down_revision: Union[str, None] = '20251216_000001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add tier column with default value 'partner'
    op.add_column('sponsors', sa.Column('tier', sa.String(20), nullable=False, server_default='partner'))
    op.create_index(op.f('ix_sponsors_tier'), 'sponsors', ['tier'], unique=False)

    # Add contact_email column
    op.add_column('sponsors', sa.Column('contact_email', sa.String(255), nullable=True))


def downgrade() -> None:
    # Remove contact_email column
    op.drop_column('sponsors', 'contact_email')

    # Remove tier column and index
    op.drop_index(op.f('ix_sponsors_tier'), table_name='sponsors')
    op.drop_column('sponsors', 'tier')
