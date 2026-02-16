"""Add audit_logs, user_devices, user_suspicious_flags tables.

Also adds ban fields + normalized_email to user_accounts.
"""

from typing import Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB

revision: str = 'audit_tracking_001'
down_revision: Union[str, None] = 'fix_fish_slugs_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── audit_logs ──
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('risk_level', sa.String(20), nullable=False, server_default='low'),
        sa.Column('ip_address', INET(), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('device_id', sa.String(255), nullable=True),
        sa.Column('details', JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_logs_id', 'audit_logs', ['id'])
    op.create_index('ix_audit_logs_user_id', 'audit_logs', ['user_id'])
    op.create_index('ix_audit_logs_event_type', 'audit_logs', ['event_type'])
    op.create_index('ix_audit_logs_device_id', 'audit_logs', ['device_id'])
    op.create_index('ix_audit_logs_ip_address', 'audit_logs', ['ip_address'])
    op.create_index('ix_audit_logs_created_at_desc', 'audit_logs', ['created_at'])

    # ── user_devices ──
    op.create_table(
        'user_devices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.String(255), nullable=False),
        sa.Column('device_name', sa.String(255), nullable=True),
        sa.Column('os', sa.String(50), nullable=True),
        sa.Column('os_version', sa.String(50), nullable=True),
        sa.Column('brand', sa.String(100), nullable=True),
        sa.Column('model', sa.String(100), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('first_ip', INET(), nullable=True),
        sa.Column('last_ip', INET(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id', 'user_id', name='uq_user_device'),
    )
    op.create_index('ix_user_devices_id', 'user_devices', ['id'])
    op.create_index('ix_user_devices_device_id', 'user_devices', ['device_id'])
    op.create_index('ix_user_devices_user_id', 'user_devices', ['user_id'])

    # ── user_suspicious_flags ──
    op.create_table(
        'user_suspicious_flags',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('flagged_user_id', sa.Integer(), nullable=False),
        sa.Column('matched_banned_user_id', sa.Integer(), nullable=False),
        sa.Column('match_types', JSONB(), nullable=False),
        sa.Column('match_details', JSONB(), nullable=True),
        sa.Column('risk_score', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('resolved_by_id', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['flagged_user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['matched_banned_user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['resolved_by_id'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_user_suspicious_flags_id', 'user_suspicious_flags', ['id'])
    op.create_index('ix_user_suspicious_flags_flagged_user_id', 'user_suspicious_flags', ['flagged_user_id'])
    op.create_index('ix_user_suspicious_flags_matched_banned_user_id', 'user_suspicious_flags', ['matched_banned_user_id'])
    op.create_index('ix_user_suspicious_flags_status', 'user_suspicious_flags', ['status'])

    # ── Add columns to user_accounts ──
    op.add_column('user_accounts', sa.Column('is_banned', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('user_accounts', sa.Column('banned_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('user_accounts', sa.Column('ban_reason', sa.String(500), nullable=True))
    op.add_column('user_accounts', sa.Column('normalized_email', sa.String(255), nullable=True))
    op.create_index('ix_user_accounts_is_banned', 'user_accounts', ['is_banned'])
    op.create_index('ix_user_accounts_normalized_email', 'user_accounts', ['normalized_email'])


def downgrade() -> None:
    # Drop user_accounts columns
    op.drop_index('ix_user_accounts_normalized_email', table_name='user_accounts')
    op.drop_index('ix_user_accounts_is_banned', table_name='user_accounts')
    op.drop_column('user_accounts', 'normalized_email')
    op.drop_column('user_accounts', 'ban_reason')
    op.drop_column('user_accounts', 'banned_at')
    op.drop_column('user_accounts', 'is_banned')

    # Drop tables
    op.drop_index('ix_user_suspicious_flags_status', table_name='user_suspicious_flags')
    op.drop_index('ix_user_suspicious_flags_matched_banned_user_id', table_name='user_suspicious_flags')
    op.drop_index('ix_user_suspicious_flags_flagged_user_id', table_name='user_suspicious_flags')
    op.drop_index('ix_user_suspicious_flags_id', table_name='user_suspicious_flags')
    op.drop_table('user_suspicious_flags')

    op.drop_index('ix_user_devices_user_id', table_name='user_devices')
    op.drop_index('ix_user_devices_device_id', table_name='user_devices')
    op.drop_index('ix_user_devices_id', table_name='user_devices')
    op.drop_table('user_devices')

    op.drop_index('ix_audit_logs_created_at_desc', table_name='audit_logs')
    op.drop_index('ix_audit_logs_ip_address', table_name='audit_logs')
    op.drop_index('ix_audit_logs_device_id', table_name='audit_logs')
    op.drop_index('ix_audit_logs_event_type', table_name='audit_logs')
    op.drop_index('ix_audit_logs_user_id', table_name='audit_logs')
    op.drop_index('ix_audit_logs_id', table_name='audit_logs')
    op.drop_table('audit_logs')
