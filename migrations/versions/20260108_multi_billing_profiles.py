"""Add multi-billing profile support.

Revision ID: multi_billing_profiles
Revises: remove_tsf_completely
Create Date: 2026-01-08

This migration enables multiple billing profiles per organizer:
- Removes UNIQUE constraint on user_id in organizer_billing_profiles
- Adds cnp column for individual organizer type (Cod Numeric Personal)
- Adds is_primary column to designate primary billing profile
- Adds billing_profile_id to events table
- Adds default_billing_profile_id to organizer_event_type_access
- Migrates existing profiles to is_primary = true
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'multi_billing_profiles'
down_revision: Union[str, None] = 'remove_tsf_completely'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =========================================================================
    # Step 1: Add new columns to organizer_billing_profiles
    # =========================================================================

    # Add cnp column (Cod Numeric Personal - 13 digits for individuals)
    op.add_column('organizer_billing_profiles',
        sa.Column('cnp', sa.String(13), nullable=True)
    )

    # Add is_primary column (to designate the primary billing profile)
    op.add_column('organizer_billing_profiles',
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default='false')
    )

    # =========================================================================
    # Step 2: Migrate existing profiles to is_primary = true
    # Each existing profile is the only one for its user, so mark as primary
    # =========================================================================
    op.execute("""
        UPDATE organizer_billing_profiles SET is_primary = true
    """)

    # =========================================================================
    # Step 3: Remove UNIQUE index on user_id
    # This allows multiple billing profiles per user
    # =========================================================================

    # Drop the unique index (in this db, it's an index not a constraint)
    op.drop_index('ix_organizer_billing_profiles_user_id', table_name='organizer_billing_profiles')

    # Recreate as non-unique index (needed for FK lookups)
    op.create_index('ix_organizer_billing_profiles_user_id', 'organizer_billing_profiles', ['user_id'])

    # =========================================================================
    # Step 4: Add billing_profile_id to events table
    # =========================================================================
    op.add_column('events',
        sa.Column('billing_profile_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_events_billing_profile_id',
        'events', 'organizer_billing_profiles',
        ['billing_profile_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_index('ix_events_billing_profile_id', 'events', ['billing_profile_id'])

    # =========================================================================
    # Step 5: Add default_billing_profile_id to organizer_event_type_access
    # =========================================================================
    op.add_column('organizer_event_type_access',
        sa.Column('default_billing_profile_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_organizer_event_type_access_default_billing_profile',
        'organizer_event_type_access', 'organizer_billing_profiles',
        ['default_billing_profile_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_index('ix_organizer_event_type_access_default_billing_profile_id',
                    'organizer_event_type_access', ['default_billing_profile_id'])


def downgrade() -> None:
    # =========================================================================
    # Reverse Step 5: Remove default_billing_profile_id from organizer_event_type_access
    # =========================================================================
    op.drop_index('ix_organizer_event_type_access_default_billing_profile_id',
                  table_name='organizer_event_type_access')
    op.drop_constraint('fk_organizer_event_type_access_default_billing_profile',
                       'organizer_event_type_access', type_='foreignkey')
    op.drop_column('organizer_event_type_access', 'default_billing_profile_id')

    # =========================================================================
    # Reverse Step 4: Remove billing_profile_id from events
    # =========================================================================
    op.drop_index('ix_events_billing_profile_id', table_name='events')
    op.drop_constraint('fk_events_billing_profile_id', 'events', type_='foreignkey')
    op.drop_column('events', 'billing_profile_id')

    # =========================================================================
    # Reverse Step 3: Restore UNIQUE index on user_id
    # NOTE: This will fail if there are duplicate user_ids (multiple profiles per user)
    # =========================================================================
    op.drop_index('ix_organizer_billing_profiles_user_id', table_name='organizer_billing_profiles')
    op.create_index('ix_organizer_billing_profiles_user_id', 'organizer_billing_profiles',
                    ['user_id'], unique=True)

    # =========================================================================
    # Reverse Step 2: No action needed (is_primary column will be dropped)
    # =========================================================================

    # =========================================================================
    # Reverse Step 1: Remove new columns from organizer_billing_profiles
    # =========================================================================
    op.drop_column('organizer_billing_profiles', 'is_primary')
    op.drop_column('organizer_billing_profiles', 'cnp')
