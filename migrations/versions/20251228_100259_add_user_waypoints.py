"""Add user waypoints table.

Revision ID: wp72a1b2c3d4
Revises: f8a9b7c6d5e4
Create Date: 2025-12-28 10:02:59

Story 7.2: Private Waypoints
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "wp72a1b2c3d4"
down_revision = "f8a9b7c6d5e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create waypoint_icons table (admin-managed)
    op.create_table(
        "waypoint_icons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("emoji", sa.String(10), nullable=True),
        sa.Column("svg_url", sa.String(500), nullable=True),
        sa.Column("is_pro_only", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_waypoint_icons_code"),
    )
    op.create_index("ix_waypoint_icons_code", "waypoint_icons", ["code"])

    # Create waypoint_categories table (admin-managed)
    op.create_table(
        "waypoint_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("color", sa.String(7), server_default="#E85D04", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_waypoint_categories_code"),
    )

    # Create user_waypoints table
    op.create_table(
        "user_waypoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        # Location
        sa.Column("latitude", sa.Numeric(10, 8), nullable=False),
        sa.Column("longitude", sa.Numeric(11, 8), nullable=False),
        # Details
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(50), server_default="pin", nullable=False),
        sa.Column("color", sa.String(7), server_default="#E85D04", nullable=False),
        sa.Column("category", sa.String(50), nullable=True),
        # Media (Pro only)
        sa.Column("photo_url", sa.String(500), nullable=True),
        # Sharing (Pro only)
        sa.Column("is_shared", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "shared_with",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        # Metadata
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_accounts.id"],
            name="fk_user_waypoints_user_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id", "latitude", "longitude", name="uq_user_waypoints_location"
        ),
    )
    op.create_index("ix_user_waypoints_user_id", "user_waypoints", ["user_id"])
    op.create_index(
        "ix_user_waypoints_user_category", "user_waypoints", ["user_id", "category"]
    )

    # Seed default waypoint icons
    op.execute("""
        INSERT INTO waypoint_icons (code, name, emoji, display_order) VALUES
            ('pin', 'Location', '📍', 0),
            ('fish', 'Fishing Spot', '🐟', 1),
            ('big_fish', 'Trophy Spot', '🎣', 2),
            ('boat', 'Boat Launch', '⛵', 3),
            ('parking', 'Parking', '🅿️', 4),
            ('shelter', 'Shelter', '🏠', 5),
            ('danger', 'Hazard', '⚠️', 6),
            ('bridge', 'Bridge', '🌉', 7),
            ('drain', 'Storm Drain', '🚿', 8),
            ('shallow', 'Shallow Water', '🌊', 9),
            ('deep', 'Deep Water', '🌀', 10),
            ('vegetation', 'Vegetation', '🌿', 11)
    """)

    # Seed default categories
    op.execute("""
        INSERT INTO waypoint_categories (code, name, color, display_order) VALUES
            ('fishing', 'Fishing Spots', '#E85D04', 0),
            ('access', 'Access Points', '#2196F3', 1),
            ('hazard', 'Hazards', '#F44336', 2),
            ('other', 'Other', '#9E9E9E', 3)
    """)


def downgrade() -> None:
    op.drop_table("user_waypoints")
    op.drop_table("waypoint_categories")
    op.drop_table("waypoint_icons")
