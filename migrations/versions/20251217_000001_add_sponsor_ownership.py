"""Add owner_id to sponsors for multi-tenancy.

Revision ID: 20251217_000001
Revises: 20251216_230346
Create Date: 2025-12-17

Sponsors can now be:
- Global (owner_id = NULL): Managed by admins, available to all events
- Organizer-owned (owner_id = user_id): Managed by specific organizer
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c5e65296fa53"
down_revision = "b4d54185eb42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add owner_id column to sponsors
    op.add_column(
        "sponsors",
        sa.Column("owner_id", sa.Integer(), nullable=True)
    )

    # Add foreign key constraint
    op.create_foreign_key(
        "fk_sponsors_owner_id",
        "sponsors",
        "user_accounts",
        ["owner_id"],
        ["id"],
        ondelete="CASCADE"
    )

    # Add index for owner_id lookups
    op.create_index("ix_sponsors_owner_id", "sponsors", ["owner_id"])

    # Drop old unique constraint on name (if exists)
    try:
        op.drop_constraint("sponsors_name_key", "sponsors", type_="unique")
    except Exception:
        pass  # Constraint may not exist

    # Create new unique constraint: name unique per owner
    # Using COALESCE(owner_id, 0) to handle NULL owner_id (global sponsors)
    op.execute("""
        CREATE UNIQUE INDEX uq_sponsors_name_owner
        ON sponsors (name, COALESCE(owner_id, 0))
    """)


def downgrade() -> None:
    # Drop new unique index
    op.execute("DROP INDEX IF EXISTS uq_sponsors_name_owner")

    # Drop index
    op.drop_index("ix_sponsors_owner_id", "sponsors")

    # Drop foreign key
    op.drop_constraint("fk_sponsors_owner_id", "sponsors", type_="foreignkey")

    # Drop column
    op.drop_column("sponsors", "owner_id")

    # Restore original unique constraint
    op.create_unique_constraint("sponsors_name_key", "sponsors", ["name"])
