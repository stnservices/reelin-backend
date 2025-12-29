"""Add disqualification fields to event_enrollments.

This migration adds:
- disqualified_by_id: FK to user who disqualified the participant
- disqualified_at: Timestamp of disqualification
- disqualification_reason: Reason for disqualification (required when disqualifying)
- reinstated_by_id: FK to user who reinstated (if reversed)
- reinstated_at: Timestamp of reinstatement
- reinstatement_reason: Reason for reinstatement

This enables:
- Organizers to disqualify participants during ongoing/finished events
- Tracking of who disqualified and when
- Reversing disqualification with audit trail

Revision ID: 20251220_000001
Revises: 20251219_000003
Create Date: 2025-12-20

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251220_000001'
down_revision = '20251219_000003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add disqualification tracking fields
    op.add_column(
        'event_enrollments',
        sa.Column('disqualified_by_id', sa.Integer(), nullable=True)
    )
    op.add_column(
        'event_enrollments',
        sa.Column('disqualified_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        'event_enrollments',
        sa.Column('disqualification_reason', sa.String(500), nullable=True)
    )

    # Add reinstatement tracking fields
    op.add_column(
        'event_enrollments',
        sa.Column('reinstated_by_id', sa.Integer(), nullable=True)
    )
    op.add_column(
        'event_enrollments',
        sa.Column('reinstated_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        'event_enrollments',
        sa.Column('reinstatement_reason', sa.String(500), nullable=True)
    )

    # Add foreign key constraints
    op.create_foreign_key(
        'fk_event_enrollments_disqualified_by_id',
        'event_enrollments',
        'user_accounts',
        ['disqualified_by_id'],
        ['id'],
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_event_enrollments_reinstated_by_id',
        'event_enrollments',
        'user_accounts',
        ['reinstated_by_id'],
        ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # Drop foreign key constraints
    op.drop_constraint('fk_event_enrollments_reinstated_by_id', 'event_enrollments', type_='foreignkey')
    op.drop_constraint('fk_event_enrollments_disqualified_by_id', 'event_enrollments', type_='foreignkey')

    # Drop reinstatement columns
    op.drop_column('event_enrollments', 'reinstatement_reason')
    op.drop_column('event_enrollments', 'reinstated_at')
    op.drop_column('event_enrollments', 'reinstated_by_id')

    # Drop disqualification columns
    op.drop_column('event_enrollments', 'disqualification_reason')
    op.drop_column('event_enrollments', 'disqualified_at')
    op.drop_column('event_enrollments', 'disqualified_by_id')
