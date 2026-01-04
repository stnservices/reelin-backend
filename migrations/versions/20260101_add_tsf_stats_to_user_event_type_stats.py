"""Add TSF-specific stats to user_event_type_stats.

This migration adds:
- tsf_total_days: Competition days participated in
- tsf_sector_wins: 1st place finishes within sector/leg
- tsf_total_catches: Total fish caught across all TSF legs
- tsf_tournament_wins: 1st place overall in TSF tournaments
- tsf_tournament_podiums: Top 3 overall finishes in TSF tournaments
- tsf_best_position_points: Best (lowest) position points total achieved

All fields are nullable to distinguish "no TSF participation" from "zero".

Revision ID: 20260101_add_tsf_stats
Revises: 20260101_add_ta_stats
Create Date: 2026-01-01

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260101_add_tsf_stats'
down_revision = '20260101_add_ta_stats'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TSF-specific statistics fields
    op.add_column('user_event_type_stats',
        sa.Column('tsf_total_days', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('tsf_sector_wins', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('tsf_total_catches', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('tsf_tournament_wins', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('tsf_tournament_podiums', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('tsf_best_position_points', sa.Integer(), nullable=True))


def downgrade() -> None:
    # Remove TSF stats columns in reverse order
    op.drop_column('user_event_type_stats', 'tsf_best_position_points')
    op.drop_column('user_event_type_stats', 'tsf_tournament_podiums')
    op.drop_column('user_event_type_stats', 'tsf_tournament_wins')
    op.drop_column('user_event_type_stats', 'tsf_total_catches')
    op.drop_column('user_event_type_stats', 'tsf_sector_wins')
    op.drop_column('user_event_type_stats', 'tsf_total_days')
