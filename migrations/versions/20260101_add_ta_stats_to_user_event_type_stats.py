"""Add TA-specific stats to user_event_type_stats.

This migration adds:
- ta_total_matches: Total head-to-head TA matches competed
- ta_match_wins: Matches won
- ta_match_losses: Matches lost
- ta_match_ties: Matches tied
- ta_total_catches: Total fish caught across all TA matches
- ta_tournament_wins: 1st place finishes in TA tournaments
- ta_tournament_podiums: Top 3 finishes in TA tournaments

All fields are nullable to distinguish "no TA participation" from "zero".

Revision ID: 20260101_add_ta_stats
Revises: 20260101_add_club_id_tsf
Create Date: 2026-01-01

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260101_add_ta_stats'
down_revision = '20260101_add_club_id_tsf'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TA-specific statistics fields
    op.add_column('user_event_type_stats',
        sa.Column('ta_total_matches', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('ta_match_wins', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('ta_match_losses', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('ta_match_ties', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('ta_total_catches', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('ta_tournament_wins', sa.Integer(), nullable=True))
    op.add_column('user_event_type_stats',
        sa.Column('ta_tournament_podiums', sa.Integer(), nullable=True))


def downgrade() -> None:
    # Remove TA stats columns in reverse order
    op.drop_column('user_event_type_stats', 'ta_tournament_podiums')
    op.drop_column('user_event_type_stats', 'ta_tournament_wins')
    op.drop_column('user_event_type_stats', 'ta_total_catches')
    op.drop_column('user_event_type_stats', 'ta_match_ties')
    op.drop_column('user_event_type_stats', 'ta_match_losses')
    op.drop_column('user_event_type_stats', 'ta_match_wins')
    op.drop_column('user_event_type_stats', 'ta_total_matches')
