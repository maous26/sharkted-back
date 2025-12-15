"""
Price History Model - Tracking historique des prix pour détection de drops.

Stocke l'historique des prix par deal pour:
- Détecter les drops (current_price vs min_price_30d)
- Calculer la volatilité des prix
- Identifier les patterns de pricing
"""
from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship

from app.models.user import Base


class PriceHistory(Base):
    """Historique des prix d'un deal."""
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, index=True)
    deal_id = Column(Integer, ForeignKey("deals.id", ondelete="CASCADE"), nullable=False)
    
    # Prix observé
    price = Column(Float, nullable=False)
    original_price = Column(Float, nullable=True)  # Prix barré si disponible
    currency = Column(String(10), default="EUR")
    
    # Métadonnées
    observed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    source_url = Column(String(500), nullable=True)
    
    # Indexes pour requêtes rapides
    __table_args__ = (
        Index('ix_price_history_deal_observed', 'deal_id', 'observed_at'),
        Index('ix_price_history_observed', 'observed_at'),
    )


class DealPriceStats(Base):
    """
    Stats de prix agrégées par deal - mise à jour à chaque scraping.
    Permet des requêtes rapides sans parcourir tout l'historique.
    """
    __tablename__ = "deal_price_stats"

    id = Column(Integer, primary_key=True, index=True)
    deal_id = Column(Integer, ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    # Prix actuels
    current_price = Column(Float, nullable=False)
    previous_price = Column(Float, nullable=True)  # Dernier prix avant le current
    
    # Stats sur 30 jours
    min_price_30d = Column(Float, nullable=True)
    max_price_30d = Column(Float, nullable=True)
    avg_price_30d = Column(Float, nullable=True)
    median_price_30d = Column(Float, nullable=True)
    
    # Stats sur 7 jours
    min_price_7d = Column(Float, nullable=True)
    max_price_7d = Column(Float, nullable=True)
    
    # Volatilité et tendance
    price_volatility = Column(Float, nullable=True)  # Coefficient de variation
    price_trend = Column(String(20), nullable=True)  # up, down, stable
    
    # Détection de drop
    is_price_drop = Column(Integer, default=0)  # 1 si drop détecté
    drop_percent = Column(Float, nullable=True)  # % de baisse vs min_30d ou previous
    drop_detected_at = Column(DateTime, nullable=True)
    
    # Compteurs
    price_changes_count = Column(Integer, default=0)
    observations_count = Column(Integer, default=1)
    
    # Timestamps
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('ix_deal_price_stats_drop', 'is_price_drop', 'drop_percent'),
        Index('ix_deal_price_stats_current', 'current_price'),
    )
