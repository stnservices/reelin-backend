"""Update enrollment triggers for enrollment_number and draw_number.

Revision ID: sf_draw_number_trigger
Revises: multi_billing_profiles
Create Date: 2026-01-11

Updates triggers for enrollment_number and draw_number:
- enrollment_number: Assigned on INSERT (not just approval), sequential (MAX+1), permanent
- draw_number: Assigned on INSERT, randomized position, permanent
- Both apply to ALL event types (SF and TA)
- No duplicates per event
- Numbers never change once assigned (no compaction on cancellation)

This enables TA events to use pre-assigned numbers for lineup generation.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'sf_draw_number_trigger'
down_revision: Union[str, None] = 'multi_billing_profiles'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the old enrollment_number trigger that only fires on approval
    op.execute("DROP TRIGGER IF EXISTS trigger_assign_enrollment_number ON event_enrollments;")
    op.execute("DROP FUNCTION IF EXISTS assign_enrollment_number();")

    # Create new combined trigger function for both enrollment_number and draw_number
    # Fires on INSERT only - numbers are permanent once assigned
    op.execute("""
        CREATE OR REPLACE FUNCTION assign_enrollment_numbers()
        RETURNS TRIGGER AS $$
        DECLARE
            v_random_position int;
            v_current_max_draw int;
        BEGIN
            -- Assign enrollment_number if not already set (sequential)
            IF NEW.enrollment_number IS NULL THEN
                SELECT COALESCE(MAX(enrollment_number), 0) + 1
                INTO NEW.enrollment_number
                FROM event_enrollments
                WHERE event_id = NEW.event_id AND enrollment_number IS NOT NULL;
            END IF;

            -- Assign draw_number if not already set (randomized)
            IF NEW.draw_number IS NULL THEN
                -- Get current max draw_number for this event (or 0 if none)
                SELECT COALESCE(MAX(draw_number), 0) INTO v_current_max_draw
                FROM event_enrollments
                WHERE event_id = NEW.event_id AND draw_number IS NOT NULL;

                -- Generate random position between 1 and (max + 1)
                v_random_position := floor(random() * (v_current_max_draw + 1)) + 1;

                -- Shift all existing enrollments with draw_number >= random_position
                UPDATE event_enrollments
                SET draw_number = draw_number + 1
                WHERE event_id = NEW.event_id
                  AND draw_number IS NOT NULL
                  AND draw_number >= v_random_position;

                -- Assign the random position to the new enrollment
                NEW.draw_number := v_random_position;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Create trigger for INSERT only
    op.execute("""
        CREATE TRIGGER trigger_assign_enrollment_numbers
        BEFORE INSERT ON event_enrollments
        FOR EACH ROW
        EXECUTE FUNCTION assign_enrollment_numbers();
    """)

    # Backfill existing enrollments that don't have enrollment_number
    # Assign based on enrolled_at timestamp (earliest gets #1)
    op.execute("""
        WITH numbered AS (
            SELECT id,
                   event_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY event_id
                       ORDER BY COALESCE(approved_at, enrolled_at), id
                   ) AS num
            FROM event_enrollments
            WHERE enrollment_number IS NULL
        )
        UPDATE event_enrollments e
        SET enrollment_number = n.num
        FROM numbered n
        WHERE e.id = n.id;
    """)

    # Backfill existing enrollments that don't have draw_number
    # Assign randomized numbers per event
    op.execute("""
        WITH randomized AS (
            SELECT id,
                   event_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY event_id
                       ORDER BY random()
                   ) AS random_num
            FROM event_enrollments
            WHERE draw_number IS NULL
        )
        UPDATE event_enrollments e
        SET draw_number = r.random_num
        FROM randomized r
        WHERE e.id = r.id;
    """)


def downgrade() -> None:
    # Drop new trigger and function
    op.execute("DROP TRIGGER IF EXISTS trigger_assign_enrollment_numbers ON event_enrollments;")
    op.execute("DROP FUNCTION IF EXISTS assign_enrollment_numbers();")

    # Restore the old enrollment_number trigger (approval-based)
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

    op.execute("""
        CREATE TRIGGER trigger_assign_enrollment_number
        BEFORE INSERT OR UPDATE ON event_enrollments
        FOR EACH ROW
        EXECUTE FUNCTION assign_enrollment_number();
    """)

    # Note: We don't clear draw_numbers on downgrade
