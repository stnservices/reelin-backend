"""Add app_settings table for dynamic configuration.

Revision ID: 20251228_220001
Revises: 20251228_210001
Create Date: 2025-12-28 22:00:01.000000

Story 8.9: App Version Checker - Admin settings support.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251228_220001'
down_revision = '20251228_210001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create app_settings table (if not exists - may have been auto-created by SQLAlchemy)
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)

    if 'app_settings' not in inspector.get_table_names():
        op.create_table(
            'app_settings',
            sa.Column('id', sa.Integer(), nullable=False, default=1),
            sa.Column('app_version', sa.String(20), nullable=False, server_default='1.0.0'),
            sa.Column('app_min_version_ios', sa.String(20), nullable=False, server_default='1.0.0'),
            sa.Column('app_min_version_android', sa.String(20), nullable=False, server_default='1.0.0'),
            sa.Column('app_store_url', sa.String(500), nullable=False,
                      server_default='https://apps.apple.com/app/reelin/id123456789'),
            sa.Column('play_store_url', sa.String(500), nullable=False,
                      server_default='https://play.google.com/store/apps/details?id=ro.reelin.app'),
            sa.Column('release_notes', sa.Text(), nullable=True),
            sa.Column('force_update_message', sa.Text(), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_by_id', sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )

    # Insert default row if not exists
    op.execute("""
        INSERT INTO app_settings (
            id, app_version, app_min_version_ios, app_min_version_android,
            app_store_url, play_store_url
        )
        VALUES (
            1, '1.0.0', '1.0.0', '1.0.0',
            'https://apps.apple.com/app/reelin/id123456789',
            'https://play.google.com/store/apps/details?id=ro.reelin.app'
        )
        ON CONFLICT (id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table('app_settings')
