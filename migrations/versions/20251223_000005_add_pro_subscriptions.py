"""Add Pro subscriptions table for Stripe integration.

Revision ID: 20251223_000005
Revises: 20251223_000004
Create Date: 2025-12-23 22:00:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251223_000005'
down_revision = '20251223_000004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create pro_subscriptions table for Stripe subscription tracking
    op.create_table(
        'pro_subscriptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('stripe_subscription_id', sa.String(255), nullable=False, unique=True),
        sa.Column('stripe_customer_id', sa.String(255), nullable=False),
        sa.Column('stripe_price_id', sa.String(255), nullable=True),
        sa.Column('plan_type', sa.String(20), nullable=False),  # 'monthly' or 'yearly'
        sa.Column('status', sa.String(50), nullable=False),  # 'active', 'canceled', 'past_due', etc.
        sa.Column('current_period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_at_period_end', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_pro_subscriptions_user_id', 'pro_subscriptions', ['user_id'])
    op.create_index('ix_pro_subscriptions_stripe_customer_id', 'pro_subscriptions', ['stripe_customer_id'])
    op.create_index('ix_pro_subscriptions_status', 'pro_subscriptions', ['status'])


def downgrade() -> None:
    op.drop_index('ix_pro_subscriptions_status', table_name='pro_subscriptions')
    op.drop_index('ix_pro_subscriptions_stripe_customer_id', table_name='pro_subscriptions')
    op.drop_index('ix_pro_subscriptions_user_id', table_name='pro_subscriptions')
    op.drop_table('pro_subscriptions')
