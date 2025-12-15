"""Add price history tables

Revision ID: 20251214_price_history
Revises: 20251214_add_vinted_and_scoring
Create Date: 2025-12-14

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '20251214_price_history'
down_revision = '20251214_add_vinted_and_scoring'
branch_labels = None
depends_on = None


def upgrade():
    # Price History table
    op.create_table(
        'price_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('deal_id', sa.Integer(), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('original_price', sa.Float(), nullable=True),
        sa.Column('currency', sa.String(10), default='EUR'),
        sa.Column('observed_at', sa.DateTime(), nullable=False),
        sa.Column('source_url', sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['deal_id'], ['deals.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_price_history_deal_observed', 'price_history', ['deal_id', 'observed_at'])
    op.create_index('ix_price_history_observed', 'price_history', ['observed_at'])
    
    # Deal Price Stats table
    op.create_table(
        'deal_price_stats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('deal_id', sa.Integer(), nullable=False),
        sa.Column('current_price', sa.Float(), nullable=False),
        sa.Column('previous_price', sa.Float(), nullable=True),
        sa.Column('min_price_30d', sa.Float(), nullable=True),
        sa.Column('max_price_30d', sa.Float(), nullable=True),
        sa.Column('avg_price_30d', sa.Float(), nullable=True),
        sa.Column('median_price_30d', sa.Float(), nullable=True),
        sa.Column('min_price_7d', sa.Float(), nullable=True),
        sa.Column('max_price_7d', sa.Float(), nullable=True),
        sa.Column('price_volatility', sa.Float(), nullable=True),
        sa.Column('price_trend', sa.String(20), nullable=True),
        sa.Column('is_price_drop', sa.Integer(), default=0),
        sa.Column('drop_percent', sa.Float(), nullable=True),
        sa.Column('drop_detected_at', sa.DateTime(), nullable=True),
        sa.Column('price_changes_count', sa.Integer(), default=0),
        sa.Column('observations_count', sa.Integer(), default=1),
        sa.Column('first_seen_at', sa.DateTime(), nullable=True),
        sa.Column('last_updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['deal_id'], ['deals.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('deal_id'),
    )
    op.create_index('ix_deal_price_stats_drop', 'deal_price_stats', ['is_price_drop', 'drop_percent'])
    op.create_index('ix_deal_price_stats_current', 'deal_price_stats', ['current_price'])


def downgrade():
    op.drop_table('deal_price_stats')
    op.drop_table('price_history')
