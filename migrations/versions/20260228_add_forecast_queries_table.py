"""Add forecast_queries table for storing solunar forecast query locations."""

from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = 'forecast_queries_001'
down_revision: Union[str, None] = 'ta_compound_idx_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'forecast_queries',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user_accounts.id', ondelete='SET NULL'), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=False),
        sa.Column('longitude', sa.Float(), nullable=False),
        sa.Column('timezone', sa.Integer(), nullable=True),
        sa.Column('days', sa.Integer(), nullable=True),
        sa.Column('score', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_forecast_queries_user_id', 'forecast_queries', ['user_id'])
    op.create_index('ix_forecast_queries_created_at', 'forecast_queries', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_forecast_queries_created_at', table_name='forecast_queries')
    op.drop_index('ix_forecast_queries_user_id', table_name='forecast_queries')
    op.drop_table('forecast_queries')
