"""Add vinted_stats and deal_scores tables

Revision ID: add_vinted_scoring
Revises: 
Create Date: 2024-12-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'add_vinted_scoring'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns to deals table
    op.add_column('deals', sa.Column('brand', sa.String(100), nullable=True))
    op.add_column('deals', sa.Column('model', sa.String(255), nullable=True))
    op.add_column('deals', sa.Column('category', sa.String(100), nullable=True))
    op.add_column('deals', sa.Column('color', sa.String(100), nullable=True))
    op.add_column('deals', sa.Column('gender', sa.String(20), nullable=True))
    op.add_column('deals', sa.Column('sizes_available', sa.JSON(), nullable=True))
    
    # Create vinted_stats table
    op.create_table(
        'vinted_stats',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('deal_id', sa.Integer(), sa.ForeignKey('deals.id'), nullable=False, unique=True),
        sa.Column('nb_listings', sa.Integer(), default=0),
        sa.Column('price_min', sa.Float(), nullable=True),
        sa.Column('price_max', sa.Float(), nullable=True),
        sa.Column('price_avg', sa.Float(), nullable=True),
        sa.Column('price_median', sa.Float(), nullable=True),
        sa.Column('price_p25', sa.Float(), nullable=True),
        sa.Column('price_p75', sa.Float(), nullable=True),
        sa.Column('coefficient_variation', sa.Float(), nullable=True),
        sa.Column('margin_euro', sa.Float(), nullable=True),
        sa.Column('margin_pct', sa.Float(), nullable=True),
        sa.Column('liquidity_score', sa.Float(), nullable=True),
        sa.Column('sample_listings', sa.JSON(), nullable=True),
        sa.Column('search_query', sa.String(255), nullable=True),
        sa.Column('computed_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    
    # Create deal_scores table
    op.create_table(
        'deal_scores',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('deal_id', sa.Integer(), sa.ForeignKey('deals.id'), nullable=False, unique=True),
        sa.Column('flip_score', sa.Float(), nullable=False),
        sa.Column('popularity_score', sa.Float(), nullable=True),
        sa.Column('liquidity_score', sa.Float(), nullable=True),
        sa.Column('margin_score', sa.Float(), nullable=True),
        sa.Column('score_breakdown', sa.JSON(), nullable=True),
        sa.Column('recommended_action', sa.String(20), nullable=True),
        sa.Column('recommended_price', sa.Float(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('explanation', sa.Text(), nullable=True),
        sa.Column('explanation_short', sa.String(255), nullable=True),
        sa.Column('risks', sa.JSON(), nullable=True),
        sa.Column('estimated_sell_days', sa.Integer(), nullable=True),
        sa.Column('model_version', sa.String(50), default='rules_v1'),
        sa.Column('computed_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    
    # Create indexes
    op.create_index('ix_deals_brand', 'deals', ['brand'])
    op.create_index('ix_deals_score', 'deals', ['score'])


def downgrade():
    op.drop_index('ix_deals_score', 'deals')
    op.drop_index('ix_deals_brand', 'deals')
    op.drop_table('deal_scores')
    op.drop_table('vinted_stats')
    op.drop_column('deals', 'sizes_available')
    op.drop_column('deals', 'gender')
    op.drop_column('deals', 'color')
    op.drop_column('deals', 'category')
    op.drop_column('deals', 'model')
    op.drop_column('deals', 'brand')
