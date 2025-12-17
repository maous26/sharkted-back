"""
StockX Stats Model - Données de marché StockX pour les deals.
"""
from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.models.user import Base

class StockXStats(Base):
    __tablename__ = "stockx_stats"
    
    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("deals.id", ondelete="CASCADE"), unique=True, index=True)
    product_name = Column(String(500))
    product_url = Column(String(1000))
    image_url = Column(String(1000))
    lowest_ask = Column(Float, default=0)
    highest_bid = Column(Float, default=0)
    last_sale = Column(Float, default=0)
    sales_last_72h = Column(Integer, default=0)
    retail_price = Column(Float, default=0)
    volatility = Column(Float, default=0)
    price_premium = Column(Float, default=0)
    margin_euro = Column(Float, default=0)
    margin_pct = Column(Float, default=0)
    liquidity_score = Column(Float, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
