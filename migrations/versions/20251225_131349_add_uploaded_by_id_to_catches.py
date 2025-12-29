"""add_uploaded_by_id_to_catches

Revision ID: 51425b6b7b09
Revises: d91bbd3bf801
Create Date: 2025-12-25 13:13:49.485892+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '51425b6b7b09'
down_revision: Union[str, None] = 'd91bbd3bf801'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add uploaded_by_id column to catches table
    op.add_column(
        'catches',
        sa.Column('uploaded_by_id', sa.Integer(), nullable=True)
    )
    # Add foreign key constraint
    op.create_foreign_key(
        'fk_catches_uploaded_by_id',
        'catches',
        'user_accounts',
        ['uploaded_by_id'],
        ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # Remove foreign key constraint
    op.drop_constraint('fk_catches_uploaded_by_id', 'catches', type_='foreignkey')
    # Remove column
    op.drop_column('catches', 'uploaded_by_id')
