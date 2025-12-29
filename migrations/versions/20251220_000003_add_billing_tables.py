"""Add billing tables for platform invoicing.

This migration adds:
- organizer_billing_profiles: Billing/legal info for organizers
- pricing_tiers: Per-organizer per-event-type pricing with versioning
- platform_invoices: Invoice records for completed events

This enables:
- Platform billing of organizers for their events
- Stripe Invoicing integration
- Per-organizer pricing configuration
- Full pricing history for audit trail

Revision ID: 20251220_000003
Revises: 20251220_000002
Create Date: 2025-12-20

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20251220_000003'
down_revision = '20251220_000002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create organizer_billing_profiles table
    op.create_table(
        'organizer_billing_profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('organizer_type', sa.String(20), nullable=False, server_default='association'),
        sa.Column('legal_name', sa.String(255), nullable=False),
        sa.Column('tax_id', sa.String(50), nullable=True),
        sa.Column('registration_number', sa.String(100), nullable=True),
        sa.Column('billing_address_line1', sa.String(255), nullable=False),
        sa.Column('billing_address_line2', sa.String(255), nullable=True),
        sa.Column('billing_city', sa.String(100), nullable=False),
        sa.Column('billing_county', sa.String(100), nullable=True),
        sa.Column('billing_postal_code', sa.String(20), nullable=False),
        sa.Column('billing_country', sa.String(2), nullable=False, server_default='RO'),
        sa.Column('billing_email', sa.String(255), nullable=False),
        sa.Column('billing_phone', sa.String(30), nullable=True),
        sa.Column('stripe_customer_id', sa.String(255), nullable=True),
        sa.Column('is_verified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_organizer_billing_profiles_id', 'organizer_billing_profiles', ['id'], unique=False)
    op.create_index('ix_organizer_billing_profiles_user_id', 'organizer_billing_profiles', ['user_id'], unique=True)
    op.create_index('ix_organizer_billing_profiles_stripe_customer_id', 'organizer_billing_profiles', ['stripe_customer_id'], unique=True)

    # Create pricing_tiers table
    op.create_table(
        'pricing_tiers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('billing_profile_id', sa.Integer(), nullable=False),
        sa.Column('event_type_id', sa.Integer(), nullable=False),
        sa.Column('pricing_model', sa.String(20), nullable=False, server_default='per_participant'),
        sa.Column('rate', sa.Numeric(10, 2), nullable=False),
        sa.Column('currency_code', sa.String(3), nullable=False, server_default='EUR'),
        sa.Column('minimum_charge', sa.Numeric(10, 2), nullable=True),
        sa.Column('effective_from', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('effective_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('superseded_by_id', sa.Integer(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['billing_profile_id'], ['organizer_billing_profiles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_type_id'], ['event_types.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['superseded_by_id'], ['pricing_tiers.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_id'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_pricing_tiers_id', 'pricing_tiers', ['id'], unique=False)
    op.create_index('ix_pricing_tiers_billing_profile_id', 'pricing_tiers', ['billing_profile_id'], unique=False)
    op.create_index('ix_pricing_tiers_event_type_id', 'pricing_tiers', ['event_type_id'], unique=False)

    # Create platform_invoices table
    op.create_table(
        'platform_invoices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('invoice_number', sa.String(50), nullable=False),
        sa.Column('billing_profile_id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('pricing_tier_id', sa.Integer(), nullable=False),
        sa.Column('pricing_model_snapshot', sa.String(20), nullable=False),
        sa.Column('rate_snapshot', sa.Numeric(10, 2), nullable=False),
        sa.Column('participant_count', sa.Integer(), nullable=False),
        sa.Column('subtotal', sa.Numeric(10, 2), nullable=False),
        sa.Column('discount_amount', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('adjustment_amount', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('adjustment_reason', sa.Text(), nullable=True),
        sa.Column('total_amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('currency_code', sa.String(3), nullable=False, server_default='EUR'),
        sa.Column('status', sa.String(20), nullable=False, server_default='draft'),
        sa.Column('stripe_invoice_id', sa.String(255), nullable=True),
        sa.Column('stripe_invoice_url', sa.String(500), nullable=True),
        sa.Column('stripe_pdf_url', sa.String(500), nullable=True),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('due_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('line_items', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['billing_profile_id'], ['organizer_billing_profiles.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['pricing_tier_id'], ['pricing_tiers.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_platform_invoices_id', 'platform_invoices', ['id'], unique=False)
    op.create_index('ix_platform_invoices_invoice_number', 'platform_invoices', ['invoice_number'], unique=True)
    op.create_index('ix_platform_invoices_billing_profile_id', 'platform_invoices', ['billing_profile_id'], unique=False)
    op.create_index('ix_platform_invoices_event_id', 'platform_invoices', ['event_id'], unique=False)
    op.create_index('ix_platform_invoices_status', 'platform_invoices', ['status'], unique=False)
    op.create_index('ix_platform_invoices_stripe_invoice_id', 'platform_invoices', ['stripe_invoice_id'], unique=True)


def downgrade() -> None:
    # Drop platform_invoices table
    op.drop_index('ix_platform_invoices_stripe_invoice_id', 'platform_invoices')
    op.drop_index('ix_platform_invoices_status', 'platform_invoices')
    op.drop_index('ix_platform_invoices_event_id', 'platform_invoices')
    op.drop_index('ix_platform_invoices_billing_profile_id', 'platform_invoices')
    op.drop_index('ix_platform_invoices_invoice_number', 'platform_invoices')
    op.drop_index('ix_platform_invoices_id', 'platform_invoices')
    op.drop_table('platform_invoices')

    # Drop pricing_tiers table
    op.drop_index('ix_pricing_tiers_event_type_id', 'pricing_tiers')
    op.drop_index('ix_pricing_tiers_billing_profile_id', 'pricing_tiers')
    op.drop_index('ix_pricing_tiers_id', 'pricing_tiers')
    op.drop_table('pricing_tiers')

    # Drop organizer_billing_profiles table
    op.drop_index('ix_organizer_billing_profiles_stripe_customer_id', 'organizer_billing_profiles')
    op.drop_index('ix_organizer_billing_profiles_user_id', 'organizer_billing_profiles')
    op.drop_index('ix_organizer_billing_profiles_id', 'organizer_billing_profiles')
    op.drop_table('organizer_billing_profiles')
