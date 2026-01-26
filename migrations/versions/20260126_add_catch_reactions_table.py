"""Add catch_reactions table for likes/dislikes on catch photos.

Allows users to react (like/dislike) to catch photos.
Each user can have at most one reaction per catch.
"""

from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = 'catch_react_001'
down_revision: Union[str, None] = 'route_hist_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'catch_reactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('catch_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('reaction_type', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['catch_id'], ['catches.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('catch_id', 'user_id', name='uq_catch_reactions_catch_user'),
    )

    # Create indexes for efficient lookups
    op.create_index('ix_catch_reactions_id', 'catch_reactions', ['id'], unique=False)
    op.create_index('ix_catch_reactions_catch_id', 'catch_reactions', ['catch_id'], unique=False)
    op.create_index('ix_catch_reactions_user_id', 'catch_reactions', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_catch_reactions_user_id', table_name='catch_reactions')
    op.drop_index('ix_catch_reactions_catch_id', table_name='catch_reactions')
    op.drop_index('ix_catch_reactions_id', table_name='catch_reactions')
    op.drop_table('catch_reactions')
