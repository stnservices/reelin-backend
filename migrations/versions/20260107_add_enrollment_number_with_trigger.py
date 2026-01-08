"""add_enrollment_number_with_trigger

Revision ID: enrollment_number_v1
Revises: 86b7d1b8cf9c
Create Date: 2026-01-07

Adds enrollment_number column and trigger to auto-assign sequential
numbers per event when enrollment is approved.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'enrollment_number_v1'
down_revision: Union[str, None] = '86b7d1b8cf9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add enrollment_number column
    op.add_column(
        'event_enrollments',
        sa.Column('enrollment_number', sa.Integer(), nullable=True)
    )
    op.create_index(
        'ix_event_enrollments_enrollment_number',
        'event_enrollments',
        ['enrollment_number']
    )

    # Create trigger function to auto-assign enrollment number on approval
    op.execute("""
        CREATE OR REPLACE FUNCTION assign_enrollment_number()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Only assign if status changed to 'approved' and enrollment_number is null
            IF NEW.status = 'approved' AND NEW.enrollment_number IS NULL THEN
                -- Get next enrollment number for this event
                SELECT COALESCE(MAX(enrollment_number), 0) + 1
                INTO NEW.enrollment_number
                FROM event_enrollments
                WHERE event_id = NEW.event_id AND enrollment_number IS NOT NULL;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Create trigger
    op.execute("""
        CREATE TRIGGER trigger_assign_enrollment_number
        BEFORE INSERT OR UPDATE ON event_enrollments
        FOR EACH ROW
        EXECUTE FUNCTION assign_enrollment_number();
    """)

    # Backfill existing approved enrollments with enrollment numbers
    # Assign numbers based on approved_at timestamp (earliest gets #1)
    op.execute("""
        WITH numbered AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY event_id
                       ORDER BY COALESCE(approved_at, enrolled_at), id
                   ) AS num
            FROM event_enrollments
            WHERE status = 'approved' AND enrollment_number IS NULL
        )
        UPDATE event_enrollments e
        SET enrollment_number = n.num
        FROM numbered n
        WHERE e.id = n.id;
    """)


def downgrade() -> None:
    # Drop trigger
    op.execute("DROP TRIGGER IF EXISTS trigger_assign_enrollment_number ON event_enrollments;")

    # Drop function
    op.execute("DROP FUNCTION IF EXISTS assign_enrollment_number();")

    # Drop index and column
    op.drop_index('ix_event_enrollments_enrollment_number', 'event_enrollments')
    op.drop_column('event_enrollments', 'enrollment_number')
