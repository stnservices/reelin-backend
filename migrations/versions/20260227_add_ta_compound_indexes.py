"""Add compound indexes for TA match/game card hot queries.

Covers the two most frequent query patterns:
- ta_matches(event_id, leg_number, status) — leg-completion check
- ta_game_cards(event_id, leg_number, status) — completed-legs count in Firebase sync
"""

from typing import Union
from alembic import op

revision: str = 'ta_compound_idx_001'
down_revision: Union[str, None] = 'remove_ads_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'idx_ta_matches_event_leg_status',
        'ta_matches',
        ['event_id', 'leg_number', 'status'],
    )
    op.create_index(
        'idx_ta_game_cards_event_leg_status',
        'ta_game_cards',
        ['event_id', 'leg_number', 'status'],
    )


def downgrade() -> None:
    op.drop_index('idx_ta_game_cards_event_leg_status', table_name='ta_game_cards')
    op.drop_index('idx_ta_matches_event_leg_status', table_name='ta_matches')
