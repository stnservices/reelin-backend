"""Add meeting_points table and location ownership fields.

Revision ID: 20251231_100001
Revises:
Create Date: 2025-12-31

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '20251231_100001'
down_revision = '20251230_100001'
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    """Check if a column exists in a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def table_exists(table_name):
    """Check if a table exists."""
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    # Add owner_id, address, and updated_at to fishing_spots (if not already there)
    if not column_exists('fishing_spots', 'owner_id'):
        op.add_column('fishing_spots', sa.Column('owner_id', sa.Integer(), nullable=True))
        op.create_foreign_key(
            'fk_fishing_spots_owner_id',
            'fishing_spots', 'user_accounts',
            ['owner_id'], ['id'],
            ondelete='CASCADE'
        )
        op.create_index('ix_fishing_spots_owner_id', 'fishing_spots', ['owner_id'])

    if not column_exists('fishing_spots', 'address'):
        op.add_column('fishing_spots', sa.Column('address', sa.String(500), nullable=True))

    if not column_exists('fishing_spots', 'updated_at'):
        op.add_column('fishing_spots', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))

    # Update existing fishing_spots to have default coordinates if missing
    # Set to 0,0 for existing records that don't have coordinates
    op.execute("""
        UPDATE fishing_spots
        SET latitude = 0.0, longitude = 0.0
        WHERE latitude IS NULL OR longitude IS NULL
    """)

    # Make latitude and longitude NOT NULL now that we have default values
    op.alter_column('fishing_spots', 'latitude',
                    existing_type=sa.Float(),
                    nullable=False)
    op.alter_column('fishing_spots', 'longitude',
                    existing_type=sa.Float(),
                    nullable=False)

    # Create meeting_points table (if not already there)
    if not table_exists('meeting_points'):
        op.create_table(
            'meeting_points',
            sa.Column('id', sa.Integer(), primary_key=True, index=True),
            sa.Column('fishing_spot_id', sa.Integer(), sa.ForeignKey('fishing_spots.id', ondelete='CASCADE'), nullable=False, index=True),
            sa.Column('owner_id', sa.Integer(), sa.ForeignKey('user_accounts.id', ondelete='CASCADE'), nullable=True, index=True),
            sa.Column('name', sa.String(200), nullable=False),
            sa.Column('address', sa.String(500), nullable=True),
            sa.Column('latitude', sa.Float(), nullable=False),
            sa.Column('longitude', sa.Float(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    # Drop meeting_points table
    op.drop_table('meeting_points')

    # Remove columns from fishing_spots
    op.drop_index('ix_fishing_spots_owner_id', 'fishing_spots')
    op.drop_constraint('fk_fishing_spots_owner_id', 'fishing_spots', type_='foreignkey')
    op.drop_column('fishing_spots', 'updated_at')
    op.drop_column('fishing_spots', 'address')
    op.drop_column('fishing_spots', 'owner_id')

    # Make latitude and longitude nullable again
    op.alter_column('fishing_spots', 'latitude',
                    existing_type=sa.Float(),
                    nullable=True)
    op.alter_column('fishing_spots', 'longitude',
                    existing_type=sa.Float(),
                    nullable=True)
