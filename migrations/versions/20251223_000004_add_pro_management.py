"""add_pro_management

Revision ID: 20251223_000004
Revises: 20251223_000003
Create Date: 2025-12-23

Add Pro subscription fields to user_accounts, pro_grants table for manual grants,
and pro_audit_log table for admin action tracking.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = '20251223_000004'
down_revision = '20251223_000003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add Pro fields to user_accounts
    op.add_column('user_accounts', sa.Column('is_pro', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('user_accounts', sa.Column('pro_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('user_accounts', sa.Column('pro_stripe_customer_id', sa.String(255), nullable=True))
    op.add_column('user_accounts', sa.Column('pro_stripe_subscription_id', sa.String(255), nullable=True))
    op.add_column('user_accounts', sa.Column('pro_plan_type', sa.String(20), nullable=True))  # 'monthly', 'yearly'
    op.add_column('user_accounts', sa.Column('pro_started_at', sa.DateTime(timezone=True), nullable=True))

    # Create indexes for Stripe IDs
    op.create_index('ix_user_accounts_pro_stripe_customer_id', 'user_accounts', ['pro_stripe_customer_id'], unique=True)
    op.create_index('ix_user_accounts_pro_stripe_subscription_id', 'user_accounts', ['pro_stripe_subscription_id'], unique=True)
    op.create_index('ix_user_accounts_is_pro', 'user_accounts', ['is_pro'])

    # Create pro_grants table for manual grants
    op.create_table(
        'pro_grants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('granted_by', sa.Integer(), nullable=False),
        sa.Column('grant_type', sa.String(30), nullable=False),  # 'manual', 'compensation', 'influencer', 'tester', 'support'
        sa.Column('duration_days', sa.Integer(), nullable=True),  # NULL for lifetime
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),  # NULL for lifetime
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by', sa.Integer(), nullable=True),
        sa.Column('revoke_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['granted_by'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['revoked_by'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_pro_grants_id', 'pro_grants', ['id'])
    op.create_index('ix_pro_grants_user_id', 'pro_grants', ['user_id'])
    op.create_index('ix_pro_grants_is_active', 'pro_grants', ['is_active'])
    op.create_index('ix_pro_grants_expires_at', 'pro_grants', ['expires_at'])

    # Create pro_audit_log table for admin action tracking
    op.create_table(
        'pro_audit_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('admin_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(50), nullable=False),  # 'grant', 'revoke', 'extend', 'cancel', 'refund'
        sa.Column('details', JSONB(), nullable=True),  # Additional action details
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('stripe_event_id', sa.String(255), nullable=True),  # For Stripe-related actions
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['admin_id'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_pro_audit_log_id', 'pro_audit_log', ['id'])
    op.create_index('ix_pro_audit_log_user_id', 'pro_audit_log', ['user_id'])
    op.create_index('ix_pro_audit_log_admin_id', 'pro_audit_log', ['admin_id'])
    op.create_index('ix_pro_audit_log_action', 'pro_audit_log', ['action'])
    op.create_index('ix_pro_audit_log_created_at', 'pro_audit_log', ['created_at'])

    # Create pro_settings table for configurable settings
    op.create_table(
        'pro_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(100), nullable=False, unique=True),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['updated_by'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_pro_settings_key', 'pro_settings', ['key'], unique=True)

    # Insert default settings
    op.execute("""
        INSERT INTO pro_settings (key, value, description) VALUES
        ('trial_duration_days', '7', 'Number of days for free trial (0 to disable)'),
        ('grace_period_days', '3', 'Days to wait after failed payment before canceling'),
        ('monthly_price_eur', '2.99', 'Monthly subscription price in EUR'),
        ('yearly_price_eur', '19.99', 'Yearly subscription price in EUR')
    """)


def downgrade() -> None:
    # Drop pro_settings table
    op.drop_index('ix_pro_settings_key', table_name='pro_settings')
    op.drop_table('pro_settings')

    # Drop pro_audit_log table
    op.drop_index('ix_pro_audit_log_created_at', table_name='pro_audit_log')
    op.drop_index('ix_pro_audit_log_action', table_name='pro_audit_log')
    op.drop_index('ix_pro_audit_log_admin_id', table_name='pro_audit_log')
    op.drop_index('ix_pro_audit_log_user_id', table_name='pro_audit_log')
    op.drop_index('ix_pro_audit_log_id', table_name='pro_audit_log')
    op.drop_table('pro_audit_log')

    # Drop pro_grants table
    op.drop_index('ix_pro_grants_expires_at', table_name='pro_grants')
    op.drop_index('ix_pro_grants_is_active', table_name='pro_grants')
    op.drop_index('ix_pro_grants_user_id', table_name='pro_grants')
    op.drop_index('ix_pro_grants_id', table_name='pro_grants')
    op.drop_table('pro_grants')

    # Drop Pro fields from user_accounts
    op.drop_index('ix_user_accounts_is_pro', table_name='user_accounts')
    op.drop_index('ix_user_accounts_pro_stripe_subscription_id', table_name='user_accounts')
    op.drop_index('ix_user_accounts_pro_stripe_customer_id', table_name='user_accounts')
    op.drop_column('user_accounts', 'pro_started_at')
    op.drop_column('user_accounts', 'pro_plan_type')
    op.drop_column('user_accounts', 'pro_stripe_subscription_id')
    op.drop_column('user_accounts', 'pro_stripe_customer_id')
    op.drop_column('user_accounts', 'pro_expires_at')
    op.drop_column('user_accounts', 'is_pro')
