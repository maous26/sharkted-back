"""Add deals table

Revision ID: add_deals_001
Revises: 314c0ca32bd9
Create Date: 2025-01-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_deals_001'
down_revision: Union[str, None] = '314c0ca32bd9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'deals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(50), nullable=False),
        sa.Column('external_id', sa.String(255), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(10), nullable=False, server_default='EUR'),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('image_url', sa.Text(), nullable=True),
        sa.Column('seller_name', sa.String(255), nullable=True),
        sa.Column('location', sa.String(255), nullable=True),
        sa.Column('original_price', sa.Float(), nullable=True),
        sa.Column('discount_percent', sa.Float(), nullable=True),
        sa.Column('in_stock', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('raw_data', sa.JSON(), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('price_updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Index sur source pour filtrage rapide
    op.create_index('ix_deals_source', 'deals', ['source'])

    # Index unique sur (source, external_id) - clé logique
    op.create_index('ix_deals_source_external_id', 'deals', ['source', 'external_id'], unique=True)

    # Index sur price pour requêtes de recherche
    op.create_index('ix_deals_price', 'deals', ['price'])

    # Index sur last_seen_at pour requêtes récentes
    op.create_index('ix_deals_last_seen', 'deals', ['last_seen_at'])


def downgrade() -> None:
    op.drop_index('ix_deals_last_seen', table_name='deals')
    op.drop_index('ix_deals_price', table_name='deals')
    op.drop_index('ix_deals_source_external_id', table_name='deals')
    op.drop_index('ix_deals_source', table_name='deals')
    op.drop_table('deals')
