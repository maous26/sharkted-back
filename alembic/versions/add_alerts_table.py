"""Add alerts table

Revision ID: add_alerts_001
Revises: add_deals_001
Create Date: 2025-12-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_alerts_001'
down_revision: Union[str, None] = 'add_deals_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('type', sa.String(50), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('deal_id', sa.String(255), nullable=True),
        sa.Column('is_read', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )

    # Index on user_id for fast user lookups
    op.create_index('ix_alerts_user_id', 'alerts', ['user_id'])

    # Index on is_read for unread queries
    op.create_index('ix_alerts_is_read', 'alerts', ['is_read'])

    # Index on created_at for sorting
    op.create_index('ix_alerts_created_at', 'alerts', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_alerts_created_at', table_name='alerts')
    op.drop_index('ix_alerts_is_read', table_name='alerts')
    op.drop_index('ix_alerts_user_id', table_name='alerts')
    op.drop_table('alerts')
