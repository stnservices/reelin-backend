"""Add composite indexes for TA match performance.

Covers hot query patterns:
- ta_matches(event_id, status) — standings/ranking queries filter by completed status
- ta_matches(event_id, phase, round_number) — schedule endpoint ordering
"""

from typing import Union
from alembic import op

revision: str = 'ta_perf_idx_001'
down_revision: Union[str, None] = 'drop_ai_analysis_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'idx_ta_matches_event_status',
        'ta_matches',
        ['event_id', 'status'],
    )
    op.create_index(
        'idx_ta_matches_event_phase_round',
        'ta_matches',
        ['event_id', 'phase', 'round_number'],
    )


def downgrade() -> None:
    op.drop_index('idx_ta_matches_event_phase_round', table_name='ta_matches')
    op.drop_index('idx_ta_matches_event_status', table_name='ta_matches')
