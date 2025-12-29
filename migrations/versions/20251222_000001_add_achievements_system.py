"""Add achievements and statistics system.

This migration adds:
- achievement_definitions: Predefined achievements (tiered and special)
- user_achievements: Records of achievements earned by users
- user_achievement_progress: Tracks progress toward tiered achievements
- user_streak_trackers: Tracks consecutive streaks (wins, podiums, participation)
- user_event_type_stats: Aggregated user statistics per event type

Seeds achievement definitions for:
- 5 tiered achievement categories (Bronze -> Silver -> Gold -> Platinum)
- 13 special unique badges

Revision ID: 20251222_000001
Revises: 20251220_000003
Create Date: 2025-12-22

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251222_000001'
down_revision = '28ac2eea2240'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create achievement_definitions table
    op.create_table(
        'achievement_definitions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(50), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('category', sa.String(20), nullable=False),  # tiered or special
        sa.Column('achievement_type', sa.String(50), nullable=False),
        sa.Column('tier', sa.String(20), nullable=True),  # bronze/silver/gold/platinum
        sa.Column('threshold', sa.Integer(), nullable=True),
        sa.Column('event_type_id', sa.Integer(), nullable=True),
        sa.Column('icon_url', sa.String(500), nullable=True),
        sa.Column('badge_color', sa.String(20), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['event_type_id'], ['event_types.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_achievement_definitions_id', 'achievement_definitions', ['id'], unique=False)
    op.create_index('ix_achievement_definitions_code', 'achievement_definitions', ['code'], unique=True)
    op.create_index('ix_achievement_definitions_event_type_id', 'achievement_definitions', ['event_type_id'], unique=False)

    # Create user_achievements table
    op.create_table(
        'user_achievements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('achievement_id', sa.Integer(), nullable=False),
        sa.Column('earned_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=True),
        sa.Column('catch_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['achievement_id'], ['achievement_definitions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['catch_id'], ['catches.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'achievement_id', name='uq_user_achievement')
    )
    op.create_index('ix_user_achievements_id', 'user_achievements', ['id'], unique=False)
    op.create_index('ix_user_achievements_user_id', 'user_achievements', ['user_id'], unique=False)
    op.create_index('ix_user_achievements_achievement_id', 'user_achievements', ['achievement_id'], unique=False)
    op.create_index('ix_user_achievements_event_id', 'user_achievements', ['event_id'], unique=False)

    # Create user_achievement_progress table
    op.create_table(
        'user_achievement_progress',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('achievement_type', sa.String(50), nullable=False),
        sa.Column('event_type_id', sa.Integer(), nullable=True),
        sa.Column('current_value', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_updated', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_type_id'], ['event_types.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'achievement_type', 'event_type_id', name='uq_user_achievement_progress')
    )
    op.create_index('ix_user_achievement_progress_id', 'user_achievement_progress', ['id'], unique=False)
    op.create_index('ix_user_achievement_progress_user_id', 'user_achievement_progress', ['user_id'], unique=False)
    op.create_index('ix_user_achievement_progress_achievement_type', 'user_achievement_progress', ['achievement_type'], unique=False)
    op.create_index('ix_user_achievement_progress_event_type_id', 'user_achievement_progress', ['event_type_id'], unique=False)

    # Create user_streak_trackers table
    op.create_table(
        'user_streak_trackers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('streak_type', sa.String(50), nullable=False),
        sa.Column('current_streak', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_streak', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_event_id', sa.Integer(), nullable=True),
        sa.Column('last_updated', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['last_event_id'], ['events.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'streak_type', name='uq_user_streak_tracker')
    )
    op.create_index('ix_user_streak_trackers_id', 'user_streak_trackers', ['id'], unique=False)
    op.create_index('ix_user_streak_trackers_user_id', 'user_streak_trackers', ['user_id'], unique=False)
    op.create_index('ix_user_streak_trackers_streak_type', 'user_streak_trackers', ['streak_type'], unique=False)

    # Create user_event_type_stats table
    op.create_table(
        'user_event_type_stats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('event_type_id', sa.Integer(), nullable=True),  # null = overall
        sa.Column('total_events', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_events_this_year', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_catches', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_approved_catches', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_rejected_catches', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_wins', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('podium_finishes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('best_rank', sa.Integer(), nullable=True),
        sa.Column('total_points', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('total_bonus_points', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_penalty_points', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('largest_catch_cm', sa.Float(), nullable=True),
        sa.Column('largest_catch_species_id', sa.Integer(), nullable=True),
        sa.Column('average_catch_length', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('unique_species_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('consecutive_events', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_consecutive_events', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_event_id', sa.Integer(), nullable=True),
        sa.Column('last_event_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_updated', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_type_id'], ['event_types.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['largest_catch_species_id'], ['fish.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['last_event_id'], ['events.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'event_type_id', name='uq_user_event_type_stats')
    )
    op.create_index('ix_user_event_type_stats_id', 'user_event_type_stats', ['id'], unique=False)
    op.create_index('ix_user_event_type_stats_user_id', 'user_event_type_stats', ['user_id'], unique=False)
    op.create_index('ix_user_event_type_stats_event_type_id', 'user_event_type_stats', ['event_type_id'], unique=False)

    # Seed achievement definitions
    op.execute("""
        INSERT INTO achievement_definitions (code, name, description, category, achievement_type, tier, threshold, badge_color, sort_order, is_active, created_at)
        VALUES
        -- TIERED: Participation (events attended)
        ('participation_bronze', 'Newcomer', 'Participate in your first fishing event', 'tiered', 'participation', 'bronze', 1, '#CD7F32', 100, true, NOW()),
        ('participation_silver', 'Regular Angler', 'Participate in 5 fishing events', 'tiered', 'participation', 'silver', 5, '#C0C0C0', 101, true, NOW()),
        ('participation_gold', 'Veteran Angler', 'Participate in 10 fishing events', 'tiered', 'participation', 'gold', 10, '#FFD700', 102, true, NOW()),
        ('participation_platinum', 'Legendary Angler', 'Participate in 25 fishing events', 'tiered', 'participation', 'platinum', 25, '#E5E4E2', 103, true, NOW()),

        -- TIERED: Catches (total approved catches)
        ('catches_bronze', 'First Catches', 'Get 10 approved catches', 'tiered', 'catch_count', 'bronze', 10, '#CD7F32', 200, true, NOW()),
        ('catches_silver', 'Active Angler', 'Get 50 approved catches', 'tiered', 'catch_count', 'silver', 50, '#C0C0C0', 201, true, NOW()),
        ('catches_gold', 'Expert Angler', 'Get 100 approved catches', 'tiered', 'catch_count', 'gold', 100, '#FFD700', 202, true, NOW()),
        ('catches_platinum', 'Master Angler', 'Get 500 approved catches', 'tiered', 'catch_count', 'platinum', 500, '#E5E4E2', 203, true, NOW()),

        -- TIERED: Species (unique species caught)
        ('species_bronze', 'Curious Fisher', 'Catch 3 different species', 'tiered', 'species_count', 'bronze', 3, '#CD7F32', 300, true, NOW()),
        ('species_silver', 'Species Explorer', 'Catch 5 different species', 'tiered', 'species_count', 'silver', 5, '#C0C0C0', 301, true, NOW()),
        ('species_gold', 'Species Collector', 'Catch 10 different species', 'tiered', 'species_count', 'gold', 10, '#FFD700', 302, true, NOW()),
        ('species_platinum', 'Ichthyologist', 'Catch 15 different species', 'tiered', 'species_count', 'platinum', 15, '#E5E4E2', 303, true, NOW()),

        -- TIERED: Podiums (top 3 finishes)
        ('podium_bronze', 'First Podium', 'Finish in top 3 for the first time', 'tiered', 'podium_count', 'bronze', 1, '#CD7F32', 400, true, NOW()),
        ('podium_silver', 'Podium Regular', 'Finish in top 3 three times', 'tiered', 'podium_count', 'silver', 3, '#C0C0C0', 401, true, NOW()),
        ('podium_gold', 'Podium Master', 'Finish in top 3 five times', 'tiered', 'podium_count', 'gold', 5, '#FFD700', 402, true, NOW()),
        ('podium_platinum', 'Podium Legend', 'Finish in top 3 ten times', 'tiered', 'podium_count', 'platinum', 10, '#E5E4E2', 403, true, NOW()),

        -- TIERED: Wins (first place finishes)
        ('wins_bronze', 'First Champion', 'Win your first event', 'tiered', 'win_count', 'bronze', 1, '#CD7F32', 500, true, NOW()),
        ('wins_silver', 'Double Champion', 'Win 2 events', 'tiered', 'win_count', 'silver', 2, '#C0C0C0', 501, true, NOW()),
        ('wins_gold', 'Triple Champion', 'Win 3 events', 'tiered', 'win_count', 'gold', 3, '#FFD700', 502, true, NOW()),
        ('wins_platinum', 'Dynasty Builder', 'Win 5 events', 'tiered', 'win_count', 'platinum', 5, '#E5E4E2', 503, true, NOW()),

        -- SPECIAL: First catch
        ('first_blood', 'First Blood', 'Submit your very first validated catch', 'special', 'first_catch', null, null, '#FF4444', 600, true, NOW()),

        -- SPECIAL: Time-based
        ('early_bird', 'Early Bird', 'First catch within 30 minutes of event start', 'special', 'early_bird', null, null, '#FFA500', 601, true, NOW()),
        ('last_minute', 'Last Minute Hero', 'Get an approved catch in the final 30 minutes of an event', 'special', 'last_minute', null, null, '#9932CC', 602, true, NOW()),
        ('speed_demon', 'Speed Demon', 'Get 5 approved catches in the first hour of an event', 'special', 'speed_demon', null, null, '#00CED1', 603, true, NOW()),

        -- SPECIAL: Quality-based
        ('trophy_hunter', 'Trophy Hunter', 'Catch a fish that is 50cm or longer', 'special', 'trophy_hunter', null, null, '#228B22', 604, true, NOW()),
        ('monster_catch', 'Monster Catch', 'Set a new personal best catch length', 'special', 'monster_catch', null, null, '#8B0000', 605, true, NOW()),
        ('precision_angler', 'Precision Angler', '90%+ of catches above minimum length in a single event (min 5 catches)', 'special', 'precision_angler', null, null, '#4169E1', 606, true, NOW()),

        -- SPECIAL: Streak-based
        ('hot_streak', 'Hot Streak', 'Finish on the podium 3 events in a row', 'special', 'hot_streak', null, null, '#FF6347', 607, true, NOW()),
        ('dominator', 'Dominator', 'Win 2 events in a row', 'special', 'dominator', null, null, '#DC143C', 608, true, NOW()),
        ('iron_man', 'Iron Man', 'Participate in 5 consecutive events', 'special', 'iron_man', null, null, '#708090', 609, true, NOW()),

        -- SPECIAL: Event performance
        ('clean_sheet', 'Clean Sheet', 'Complete an event with no rejected catches (min 3 catches)', 'special', 'clean_sheet', null, null, '#32CD32', 610, true, NOW()),
        ('comeback_king', 'Comeback King', 'Improve your rank by 5 or more positions during an event', 'special', 'comeback_king', null, null, '#FF8C00', 611, true, NOW()),
        ('diversity_master', 'Diversity Master', 'Catch every available species in a single event', 'special', 'diversity_master', null, null, '#9400D3', 612, true, NOW());
    """)


def downgrade() -> None:
    # Drop user_event_type_stats table
    op.drop_index('ix_user_event_type_stats_event_type_id', 'user_event_type_stats')
    op.drop_index('ix_user_event_type_stats_user_id', 'user_event_type_stats')
    op.drop_index('ix_user_event_type_stats_id', 'user_event_type_stats')
    op.drop_table('user_event_type_stats')

    # Drop user_streak_trackers table
    op.drop_index('ix_user_streak_trackers_streak_type', 'user_streak_trackers')
    op.drop_index('ix_user_streak_trackers_user_id', 'user_streak_trackers')
    op.drop_index('ix_user_streak_trackers_id', 'user_streak_trackers')
    op.drop_table('user_streak_trackers')

    # Drop user_achievement_progress table
    op.drop_index('ix_user_achievement_progress_event_type_id', 'user_achievement_progress')
    op.drop_index('ix_user_achievement_progress_achievement_type', 'user_achievement_progress')
    op.drop_index('ix_user_achievement_progress_user_id', 'user_achievement_progress')
    op.drop_index('ix_user_achievement_progress_id', 'user_achievement_progress')
    op.drop_table('user_achievement_progress')

    # Drop user_achievements table
    op.drop_index('ix_user_achievements_event_id', 'user_achievements')
    op.drop_index('ix_user_achievements_achievement_id', 'user_achievements')
    op.drop_index('ix_user_achievements_user_id', 'user_achievements')
    op.drop_index('ix_user_achievements_id', 'user_achievements')
    op.drop_table('user_achievements')

    # Drop achievement_definitions table (will cascade delete seeded data)
    op.drop_index('ix_achievement_definitions_event_type_id', 'achievement_definitions')
    op.drop_index('ix_achievement_definitions_code', 'achievement_definitions')
    op.drop_index('ix_achievement_definitions_id', 'achievement_definitions')
    op.drop_table('achievement_definitions')
