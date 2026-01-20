"""normalize_user_names

Revision ID: 8de901c15bfd
Revises: update_rankings_exclude_test
Create Date: 2026-01-20 11:22:05.330914+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8de901c15bfd'
down_revision: Union[str, None] = 'update_rankings_exclude_test'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Normalize existing user names:
    - first_name: Title Case (INITCAP in PostgreSQL)
    - last_name: UPPERCASE (UPPER in PostgreSQL)
    """
    # Update first_name to Title Case
    op.execute("""
        UPDATE user_profiles
        SET first_name = INITCAP(TRIM(first_name))
        WHERE first_name IS NOT NULL
          AND first_name != INITCAP(TRIM(first_name))
    """)

    # Update last_name to UPPERCASE
    op.execute("""
        UPDATE user_profiles
        SET last_name = UPPER(TRIM(last_name))
        WHERE last_name IS NOT NULL
          AND last_name != UPPER(TRIM(last_name))
    """)


def downgrade() -> None:
    # No downgrade - name normalization is not reversible
    # (we don't know the original casing)
    pass
