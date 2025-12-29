"""Initial schema creation

Revision ID: 20251206_000001
Revises:
Create Date: 2025-12-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20251206_000001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Countries
    op.create_table('countries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('code', sa.String(length=3), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
        sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_countries_id'), 'countries', ['id'], unique=False)

    # Cities
    op.create_table('cities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('country_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['country_id'], ['countries.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cities_id'), 'cities', ['id'], unique=False)
    op.create_index(op.f('ix_cities_country_id'), 'cities', ['country_id'], unique=False)

    # Fishing Spots
    op.create_table('fishing_spots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('city_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=True),
        sa.Column('longitude', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['city_id'], ['cities.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_fishing_spots_id'), 'fishing_spots', ['id'], unique=False)
    op.create_index(op.f('ix_fishing_spots_city_id'), 'fishing_spots', ['city_id'], unique=False)

    # User Accounts
    op.create_table('user_accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_staff', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_superuser', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_verified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_accounts_id'), 'user_accounts', ['id'], unique=False)
    op.create_index(op.f('ix_user_accounts_email'), 'user_accounts', ['email'], unique=True)

    # User Profiles
    op.create_table('user_profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=False),
        sa.Column('last_name', sa.String(length=100), nullable=False),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('bio', sa.Text(), nullable=True),
        sa.Column('profile_picture_url', sa.String(length=500), nullable=True),
        sa.Column('roles', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('country_id', sa.Integer(), nullable=True),
        sa.Column('city_id', sa.Integer(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['country_id'], ['countries.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['city_id'], ['cities.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index(op.f('ix_user_profiles_id'), 'user_profiles', ['id'], unique=False)

    # Token Blacklist
    op.create_table('token_blacklist',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token_jti', sa.String(length=255), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_type', sa.String(length=20), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('blacklisted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_token_blacklist_id'), 'token_blacklist', ['id'], unique=False)
    op.create_index(op.f('ix_token_blacklist_token_jti'), 'token_blacklist', ['token_jti'], unique=True)

    # Fish
    op.create_table('fish',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('scientific_name', sa.String(length=150), nullable=True),
        sa.Column('min_length', sa.Float(), nullable=True),
        sa.Column('max_length', sa.Float(), nullable=True),
        sa.Column('image_url', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_fish_id'), 'fish', ['id'], unique=False)

    # Event Types
    op.create_table('event_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('icon_url', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
        sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_event_types_id'), 'event_types', ['id'], unique=False)

    # Scoring Configs
    op.create_table('scoring_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_type_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('rules', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['event_type_id'], ['event_types.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_scoring_configs_id'), 'scoring_configs', ['id'], unique=False)
    op.create_index(op.f('ix_scoring_configs_event_type_id'), 'scoring_configs', ['event_type_id'], unique=False)

    # Events
    op.create_table('events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('slug', sa.String(length=250), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('event_type_id', sa.Integer(), nullable=False),
        sa.Column('scoring_config_id', sa.Integer(), nullable=False),
        sa.Column('start_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('registration_deadline', sa.DateTime(timezone=True), nullable=True),
        sa.Column('location_id', sa.Integer(), nullable=True),
        sa.Column('location_name', sa.String(length=200), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='draft'),
        sa.Column('max_participants', sa.Integer(), nullable=True),
        sa.Column('requires_approval', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('rules', sa.Text(), nullable=True),
        sa.Column('image_url', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['event_type_id'], ['event_types.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['scoring_config_id'], ['scoring_configs.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['location_id'], ['fishing_spots.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_id'], ['user_accounts.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_events_id'), 'events', ['id'], unique=False)
    op.create_index(op.f('ix_events_slug'), 'events', ['slug'], unique=True)
    op.create_index(op.f('ix_events_status'), 'events', ['status'], unique=False)
    op.create_index(op.f('ix_events_event_type_id'), 'events', ['event_type_id'], unique=False)
    op.create_index(op.f('ix_events_scoring_config_id'), 'events', ['scoring_config_id'], unique=False)
    op.create_index(op.f('ix_events_created_by_id'), 'events', ['created_by_id'], unique=False)

    # Event Prizes
    op.create_table('event_prizes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('place', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('image_url', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_event_prizes_id'), 'event_prizes', ['id'], unique=False)
    op.create_index(op.f('ix_event_prizes_event_id'), 'event_prizes', ['event_id'], unique=False)

    # Event Scoring Rules
    op.create_table('event_scoring_rules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('fish_id', sa.Integer(), nullable=True),
        sa.Column('min_length', sa.Float(), nullable=True),
        sa.Column('max_length', sa.Float(), nullable=True),
        sa.Column('points_per_cm', sa.Float(), nullable=True),
        sa.Column('bonus_points', sa.Float(), nullable=True),
        sa.Column('points_formula', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['fish_id'], ['fish.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_event_scoring_rules_id'), 'event_scoring_rules', ['id'], unique=False)
    op.create_index(op.f('ix_event_scoring_rules_event_id'), 'event_scoring_rules', ['event_id'], unique=False)
    op.create_index(op.f('ix_event_scoring_rules_fish_id'), 'event_scoring_rules', ['fish_id'], unique=False)

    # Event Enrollments
    op.create_table('event_enrollments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('draw_number', sa.Integer(), nullable=True),
        sa.Column('approved_by_id', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rejection_reason', sa.String(length=500), nullable=True),
        sa.Column('enrolled_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['approved_by_id'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_event_enrollments_id'), 'event_enrollments', ['id'], unique=False)
    op.create_index(op.f('ix_event_enrollments_event_id'), 'event_enrollments', ['event_id'], unique=False)
    op.create_index(op.f('ix_event_enrollments_user_id'), 'event_enrollments', ['user_id'], unique=False)
    op.create_index(op.f('ix_event_enrollments_status'), 'event_enrollments', ['status'], unique=False)

    # Catches
    op.create_table('catches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('fish_id', sa.Integer(), nullable=False),
        sa.Column('length', sa.Float(), nullable=False),
        sa.Column('weight', sa.Float(), nullable=True),
        sa.Column('photo_url', sa.String(length=500), nullable=False),
        sa.Column('thumbnail_url', sa.String(length=500), nullable=True),
        sa.Column('location_lat', sa.Float(), nullable=True),
        sa.Column('location_lng', sa.Float(), nullable=True),
        sa.Column('points', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('validated_by_id', sa.Integer(), nullable=True),
        sa.Column('validated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('catch_time', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['fish_id'], ['fish.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['validated_by_id'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_catches_id'), 'catches', ['id'], unique=False)
    op.create_index(op.f('ix_catches_event_id'), 'catches', ['event_id'], unique=False)
    op.create_index(op.f('ix_catches_user_id'), 'catches', ['user_id'], unique=False)
    op.create_index(op.f('ix_catches_fish_id'), 'catches', ['fish_id'], unique=False)
    op.create_index(op.f('ix_catches_status'), 'catches', ['status'], unique=False)

    # Event Scoreboards
    op.create_table('event_scoreboards',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('total_catches', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_length', sa.Float(), nullable=False, server_default='0'),
        sa.Column('total_weight', sa.Float(), nullable=True),
        sa.Column('total_points', sa.Float(), nullable=False, server_default='0'),
        sa.Column('best_catch_length', sa.Float(), nullable=True),
        sa.Column('best_catch_id', sa.Integer(), nullable=True),
        sa.Column('rank', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('previous_rank', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['best_catch_id'], ['catches.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_event_scoreboards_id'), 'event_scoreboards', ['id'], unique=False)
    op.create_index(op.f('ix_event_scoreboards_event_id'), 'event_scoreboards', ['event_id'], unique=False)
    op.create_index(op.f('ix_event_scoreboards_user_id'), 'event_scoreboards', ['user_id'], unique=False)
    op.create_index(op.f('ix_event_scoreboards_rank'), 'event_scoreboards', ['rank'], unique=False)

    # Ranking Movements
    op.create_table('ranking_movements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('old_rank', sa.Integer(), nullable=False),
        sa.Column('new_rank', sa.Integer(), nullable=False),
        sa.Column('catch_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['catch_id'], ['catches.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ranking_movements_id'), 'ranking_movements', ['id'], unique=False)
    op.create_index(op.f('ix_ranking_movements_event_id'), 'ranking_movements', ['event_id'], unique=False)
    op.create_index(op.f('ix_ranking_movements_user_id'), 'ranking_movements', ['user_id'], unique=False)

    # Clubs
    op.create_table('clubs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('acronym', sa.String(length=20), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('logo_url', sa.String(length=500), nullable=True),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['user_accounts.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('acronym'),
        sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_clubs_id'), 'clubs', ['id'], unique=False)
    op.create_index(op.f('ix_clubs_owner_id'), 'clubs', ['owner_id'], unique=False)

    # Club Memberships
    op.create_table('club_memberships',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('club_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False, server_default='member'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='invited'),
        sa.Column('permissions', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('invited_by_id', sa.Integer(), nullable=True),
        sa.Column('invited_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('joined_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['club_id'], ['clubs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['invited_by_id'], ['user_accounts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_club_memberships_id'), 'club_memberships', ['id'], unique=False)
    op.create_index(op.f('ix_club_memberships_club_id'), 'club_memberships', ['club_id'], unique=False)
    op.create_index(op.f('ix_club_memberships_user_id'), 'club_memberships', ['user_id'], unique=False)
    op.create_index(op.f('ix_club_memberships_status'), 'club_memberships', ['status'], unique=False)

    # Notifications
    op.create_table('notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('is_read', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notifications_id'), 'notifications', ['id'], unique=False)
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)
    op.create_index(op.f('ix_notifications_type'), 'notifications', ['type'], unique=False)
    op.create_index(op.f('ix_notifications_is_read'), 'notifications', ['is_read'], unique=False)
    op.create_index(op.f('ix_notifications_created_at'), 'notifications', ['created_at'], unique=False)

    # Sponsors
    op.create_table('sponsors',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('logo_url', sa.String(length=500), nullable=True),
        sa.Column('website_url', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_sponsors_id'), 'sponsors', ['id'], unique=False)


def downgrade() -> None:
    op.drop_table('sponsors')
    op.drop_table('notifications')
    op.drop_table('club_memberships')
    op.drop_table('clubs')
    op.drop_table('ranking_movements')
    op.drop_table('event_scoreboards')
    op.drop_table('catches')
    op.drop_table('event_enrollments')
    op.drop_table('event_scoring_rules')
    op.drop_table('event_prizes')
    op.drop_table('events')
    op.drop_table('scoring_configs')
    op.drop_table('event_types')
    op.drop_table('fish')
    op.drop_table('token_blacklist')
    op.drop_table('user_profiles')
    op.drop_table('user_accounts')
    op.drop_table('fishing_spots')
    op.drop_table('cities')
    op.drop_table('countries')
